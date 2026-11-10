"""Carga de datasets y panel de diagnóstico."""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import catalogo_hallazgos as cat
from app.api.dependencias import fecha_de_corte, sesion_db, snapshot_existente
from app.api.esquemas import (
    CategoriaResumen,
    DetalleHallazgo,
    ExcepcionSalida,
    HallazgoSalida,
    PedidoExcepcion,
    PedidoGeneracionDataset,
    RechazoSalida,
    ResultadoCarga,
    ResumenDiagnostico,
    SnapshotResumido,
)
from app.modulos import excepciones
from app.modulos.analisis import ParametrosAnalisis, analizar, resumen
from app.modulos.importacion import ARCHIVOS, ErrorImportacion, importar_dataset
from app.nucleo.config import obtener_config
from app.nucleo.enums import PESO_SEVERIDAD, Severidad
from app.nucleo.modelos import ExcepcionHallazgo, Hallazgo, Snapshot, Usuario

router = APIRouter(prefix="/api/snapshots", tags=["diagnostico"])


def _parametros(snapshot: Snapshot) -> ParametrosAnalisis:
    config = obtener_config()
    return ParametrosAnalisis(
        fecha_corte=fecha_de_corte(snapshot),
        dias_cuenta_dormida=config.dias_cuenta_dormida,
        factor_sobre_privilegio=config.factor_sobreprivilegio,
        antiguedad_creep_dias=config.antiguedad_creep_dias,
        minimo_pares=config.minimo_pares_sobreprivilegio,
    )


def _a_resumido(sesion: Session, s: Snapshot) -> SnapshotResumido:
    hallazgos = sesion.scalar(
        select(func.count(Hallazgo.id)).where(Hallazgo.snapshot_id == s.id)
    )
    return SnapshotResumido(
        id=s.id,
        etiqueta=s.etiqueta,
        origen_archivo=s.origen_archivo,
        filas_leidas=s.filas_leidas,
        filas_descartadas=s.filas_descartadas,
        creado_en=s.creado_en,
        analizado=bool(hallazgos),
        usuarios=sesion.scalar(select(func.count(Usuario.id))) or 0,
    )


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SnapshotResumido])
def listar(sesion: Session = Depends(sesion_db)) -> list[SnapshotResumido]:
    """Snapshots cargados, del más reciente al más viejo."""
    filas = sesion.scalars(select(Snapshot).order_by(Snapshot.id.desc())).all()
    return [_a_resumido(sesion, s) for s in filas]


@router.post("/generar", response_model=ResultadoCarga, status_code=201)
def generar_y_cargar(
    pedido: PedidoGeneracionDataset, sesion: Session = Depends(sesion_db)
) -> ResultadoCarga:
    """Genera un conjunto sintético y lo importa, en un paso.

    Es el camino rápido para tener el sistema con datos encima sin salir de
    la pantalla. Los datos son sintéticos y la pantalla lo dice: no hay
    información de ninguna organización real en ninguna parte del sistema.
    """
    from herramientas.generar_dataset import generar

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        carpeta = Path(tmp)
        try:
            generar(pedido.perfil, carpeta)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        # El generador escribe dentro de una subcarpeta con el id del perfil.
        return _importar(
            sesion,
            carpeta / pedido.perfil,
            pedido.etiqueta or f"sintetico-{pedido.perfil}",
            origen=(
                f"Conjunto sintético {pedido.perfil}, generado por el propio "
                f"sistema. No contiene datos de ninguna organización real."
            ),
        )


@router.post("/importar", response_model=ResultadoCarga, status_code=201)
async def importar(
    archivos: list[UploadFile],
    etiqueta: str | None = None,
    sesion: Session = Depends(sesion_db),
) -> ResultadoCarga:
    """Importa los CSV de un export.

    Se esperan los archivos del esquema documentado. Los que no pertenecen
    al esquema se ignoran en lugar de cortar la carga: un export real suele
    traer archivos de más.
    """
    esperados = set(ARCHIVOS)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        carpeta = Path(tmp)
        recibidos = set()
        for archivo in archivos:
            nombre = Path(archivo.filename or "").name
            if nombre not in esperados:
                continue
            destino = carpeta / nombre
            with destino.open("wb") as salida:
                shutil.copyfileobj(archivo.file, salida)
            recibidos.add(nombre)

        faltantes = sorted(esperados - recibidos)
        if faltantes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"faltan archivos del esquema: {', '.join(faltantes)}",
            )
        return _importar(
            sesion,
            carpeta,
            etiqueta or "carga-manual",
            origen=f"Carga manual de {len(recibidos)} archivos desde el panel",
        )


def _importar(
    sesion: Session, carpeta: Path, etiqueta: str, origen: str | None = None
) -> ResultadoCarga:
    inicio = time.perf_counter()
    try:
        resultado = importar_dataset(
            sesion, carpeta, etiqueta=etiqueta, actor="panel", origen=origen
        )
    except ErrorImportacion as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return ResultadoCarga(
        snapshot_id=resultado.snapshot_id,
        etiqueta=resultado.etiqueta,
        leidas=resultado.leidas,
        aceptadas=resultado.aceptadas,
        rechazadas=len(resultado.rechazos),
        rechazos=[
            RechazoSalida(
                archivo=r.archivo, linea=r.linea, motivo=r.motivo, valor=r.valor
            )
            for r in resultado.rechazos
        ],
        conteos=resultado.conteos,
        segundos=round(time.perf_counter() - inicio, 3),
    )


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------


@router.post("/{snapshot_id}/analizar", response_model=ResumenDiagnostico)
def ejecutar_analisis(
    snapshot: Snapshot = Depends(snapshot_existente),
    sesion: Session = Depends(sesion_db),
) -> ResumenDiagnostico:
    """Corre las cinco reglas sobre el snapshot y devuelve el panel.

    Un snapshot ya analizado se vuelve a analizar desde cero: se borran sus
    hallazgos anteriores. Acumularlos duplicaría cada registro y el panel
    contaría dos veces lo mismo.
    """
    previos = sesion.scalars(
        select(Hallazgo).where(Hallazgo.snapshot_id == snapshot.id)
    ).all()
    for h in previos:
        sesion.delete(h)
    sesion.flush()

    analizar(sesion, snapshot.id, _parametros(snapshot), actor="panel")
    sesion.flush()
    return _panel(sesion, snapshot)


@router.get("/{snapshot_id}/resumen", response_model=ResumenDiagnostico)
def ver_resumen(
    snapshot: Snapshot = Depends(snapshot_existente),
    sesion: Session = Depends(sesion_db),
) -> ResumenDiagnostico:
    """Panel de diagnóstico del snapshot."""
    return _panel(sesion, snapshot)


def _panel(sesion: Session, snapshot: Snapshot) -> ResumenDiagnostico:
    todos = list(
        sesion.scalars(select(Hallazgo).where(Hallazgo.snapshot_id == snapshot.id)).all()
    )

    # Los hallazgos exceptuados se descuentan del panel, no del motor. El
    # motor sigue emitiéndolos: lo que mide el indicador I-02 es su
    # capacidad de detección, y filtrar ahí convertiría una decisión
    # administrativa en un falso negativo del algoritmo.
    anotados = excepciones.anotar(todos, excepciones.vigentes(sesion))
    hallazgos = [a.hallazgo for a in anotados if not a.exceptuado]
    exceptuados = len(todos) - len(hallazgos)

    usuarios = sesion.scalar(select(func.count(Usuario.id))) or 0
    agregados = resumen(hallazgos, usuarios)

    por_categoria: dict[str, list[Hallazgo]] = {c: [] for c in cat.ORDEN}
    for h in hallazgos:
        por_categoria.setdefault(str(h.categoria), []).append(h)

    categorias = []
    for codigo in cat.ORDEN:
        grupo = por_categoria.get(codigo, [])
        peor = Severidad.BAJA
        reparto: dict[str, int] = {}
        for h in grupo:
            s = Severidad(str(h.severidad))
            reparto[str(s)] = reparto.get(str(s), 0) + 1
            if PESO_SEVERIDAD[s] > PESO_SEVERIDAD[peor]:
                peor = s
        categorias.append(
            CategoriaResumen(
                categoria=codigo,
                titulo=cat.TITULOS[codigo],
                descripcion=cat.DESCRIPCIONES[codigo],
                severidad_maxima=str(peor) if grupo else "ninguna",
                por_severidad=reparto,
                hallazgos=len(grupo),
                usuarios=len({h.usuario_id for h in grupo if h.usuario_id}),
            )
        )

    dormidas = next(
        (c.usuarios for c in categorias if c.categoria == "cuenta_dormida"), 0
    )

    return ResumenDiagnostico(
        snapshot=_a_resumido(sesion, snapshot),
        indice_riesgo=agregados["indice_riesgo"],
        nivel_riesgo=agregados["nivel_riesgo"],
        hallazgos=len(hallazgos),
        criticos=agregados["por_severidad"].get("critica", 0),
        cuentas_dormidas=dormidas,
        usuarios_analizados=usuarios,
        usuarios_senalados=agregados["usuarios_senalados"],
        por_severidad=agregados["por_severidad"],
        categorias=categorias,
        exceptuados=exceptuados,
        analizado=bool(todos),
    )


@router.get("/{snapshot_id}/hallazgos", response_model=list[HallazgoSalida])
def listar_hallazgos(
    categoria: str | None = None,
    severidad: str | None = None,
    incluir_exceptuados: bool = False,
    limite: int = 200,
    snapshot: Snapshot = Depends(snapshot_existente),
    sesion: Session = Depends(sesion_db),
) -> list[HallazgoSalida]:
    """Hallazgos del snapshot, ordenados por riesgo y filtrables.

    Los exceptuados quedan afuera salvo que se los pida: ese es el efecto
    que define el requerimiento. No desaparecen, se los puede listar.
    """
    consulta = select(Hallazgo).where(Hallazgo.snapshot_id == snapshot.id)
    if categoria:
        consulta = consulta.where(Hallazgo.categoria == categoria)
    if severidad:
        consulta = consulta.where(Hallazgo.severidad == severidad)
    consulta = consulta.order_by(Hallazgo.puntaje.desc())

    anotados = excepciones.anotar(
        list(sesion.scalars(consulta).all()), excepciones.vigentes(sesion)
    )
    if not incluir_exceptuados:
        anotados = [a for a in anotados if not a.exceptuado]

    return [_a_salida(sesion, a) for a in anotados[:limite]]


def _a_salida(sesion: Session, a: excepciones.HallazgoAnotado) -> HallazgoSalida:
    h = a.hallazgo
    usuario = sesion.get(Usuario, h.usuario_id) if h.usuario_id else None
    evidencia = dict(h.evidencia or {})
    return HallazgoSalida(
        id=h.id,
        categoria=str(h.categoria),
        titulo=cat.TITULOS.get(str(h.categoria), str(h.categoria)),
        severidad=str(h.severidad),
        puntaje=float(h.puntaje),
        legajo=usuario.legajo if usuario else evidencia.get("legajo"),
        nombre=usuario.nombre if usuario else None,
        area=usuario.area if usuario else None,
        puesto=usuario.puesto if usuario else None,
        permiso=h.permiso.codigo if h.permiso else None,
        regla=h.regla_sod.codigo if h.regla_sod else None,
        recomendacion=h.recomendacion,
        evidencia=evidencia,
        detectado_en=h.detectado_en,
        exceptuado=a.exceptuado,
        excepcion=(
            ExcepcionSalida(
                id=a.excepcion.id,
                motivo=a.excepcion.motivo,
                responsable=a.excepcion.responsable,
                alcance="regla" if a.excepcion.regla_sod_id else "categoria",
                creada_en=a.excepcion.creada_en,
            )
            if a.excepcion is not None
            else None
        ),
    )


@router.get("/{snapshot_id}/hallazgos/{hallazgo_id}", response_model=DetalleHallazgo)
def ver_hallazgo(
    hallazgo_id: int,
    snapshot: Snapshot = Depends(snapshot_existente),
    sesion: Session = Depends(sesion_db),
) -> DetalleHallazgo:
    """Detalle de un hallazgo con los cinco elementos que pide RF-10.

    Regla aplicada, umbral, fuente, fecha del snapshot y contexto del
    acceso. El requerimiento salió de la validación con usuarios: ver que
    un hallazgo existe no alcanza para decidir qué hacer con él.
    """
    h = sesion.get(Hallazgo, hallazgo_id)
    if h is None or h.snapshot_id != snapshot.id:
        raise HTTPException(status_code=404, detail="no existe ese hallazgo")

    anotado = excepciones.anotar([h], excepciones.vigentes(sesion))[0]
    categoria = str(h.categoria)
    usuario = sesion.get(Usuario, h.usuario_id) if h.usuario_id else None
    contexto: dict[str, object] = dict(h.evidencia or {})
    if usuario is not None:
        contexto |= {
            "legajo": usuario.legajo,
            "nombre": usuario.nombre,
            "area": usuario.area,
            "puesto": usuario.puesto,
            "estado": str(usuario.estado),
            "ultimo_acceso": (
                usuario.ultimo_acceso.isoformat() if usuario.ultimo_acceso else None
            ),
        }

    return DetalleHallazgo(
        hallazgo=_a_salida(sesion, anotado),
        regla_aplicada=cat.DESCRIPCIONES.get(categoria, categoria),
        umbral=cat.umbral_de(categoria, _parametros(snapshot)),
        fuente=snapshot.origen_archivo or snapshot.etiqueta,
        fecha_snapshot=snapshot.creado_en,
        contexto=contexto,
    )


# ---------------------------------------------------------------------------
# Excepciones justificadas (RF-11)
# ---------------------------------------------------------------------------


@router.post(
    "/{snapshot_id}/hallazgos/{hallazgo_id}/excepcion",
    response_model=HallazgoSalida,
    status_code=201,
)
def exceptuar(
    hallazgo_id: int,
    pedido: PedidoExcepcion,
    snapshot: Snapshot = Depends(snapshot_existente),
    sesion: Session = Depends(sesion_db),
) -> HallazgoSalida:
    """Marca un hallazgo como excepción justificada.

    Un acceso que en los datos se ve riesgoso puede estar justificado por
    el negocio. El requerimiento salió de la encuesta de validación, donde
    el riesgo señalado fue precisamente recomendar remediación sin
    contexto. El motivo es obligatorio: una excepción sin justificación
    escrita es indistinguible de haber apagado el hallazgo porque molestaba.
    """
    h = sesion.get(Hallazgo, hallazgo_id)
    if h is None or h.snapshot_id != snapshot.id:
        raise HTTPException(status_code=404, detail="no existe ese hallazgo")

    try:
        excepciones.registrar(
            sesion,
            h,
            motivo=pedido.motivo,
            responsable=pedido.responsable,
            solo_esta_regla=pedido.solo_esta_regla,
        )
    except excepciones.ErrorExcepcion as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    sesion.flush()
    return _a_salida(sesion, excepciones.anotar([h], excepciones.vigentes(sesion))[0])


@router.get("/{snapshot_id}/excepciones", response_model=list[ExcepcionSalida])
def listar_excepciones(
    snapshot: Snapshot = Depends(snapshot_existente),
    sesion: Session = Depends(sesion_db),
) -> list[ExcepcionSalida]:
    """Excepciones vigentes, con su motivo y su responsable."""
    return [
        ExcepcionSalida(
            id=e.id,
            motivo=e.motivo,
            responsable=e.responsable,
            alcance="regla" if e.regla_sod_id else "categoria",
            creada_en=e.creada_en,
            legajo=e.usuario.legajo if e.usuario else None,
            categoria=e.categoria,
            regla=e.regla_sod.codigo if e.regla_sod else None,
        )
        for e in excepciones.vigentes(sesion)
    ]


@router.delete("/{snapshot_id}/excepciones/{excepcion_id}")
def revocar_excepcion(
    excepcion_id: int,
    actor: str,
    snapshot: Snapshot = Depends(snapshot_existente),
    sesion: Session = Depends(sesion_db),
) -> dict[str, str]:
    """Da de baja una excepción. No la borra: queda el rastro."""
    excepcion = sesion.get(ExcepcionHallazgo, excepcion_id)
    if excepcion is None:
        raise HTTPException(status_code=404, detail="no existe esa excepción")
    try:
        excepciones.revocar(sesion, excepcion, actor=actor)
    except excepciones.ErrorExcepcion as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"estado": "revocada"}
