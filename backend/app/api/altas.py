"""Altas de usuario: resolución de accesos, generación y aprobación.

Es la pantalla de la Figura 4.5 del documento. El orden de las operaciones
no es casual y conviene que se note en la API: primero el motor de reglas
resuelve qué accesos corresponden, después se genera el script a partir de
esa resolución ya cerrada, y recién al final una persona aprueba. El modelo
de lenguaje entra en el segundo paso y no puede alterar el primero.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencias import sesion_db
from app.api.esquemas import (
    ExclusionSalida,
    PedidoAlta,
    PedidoAprobacion,
    PedidoGeneracionScript,
    PermisoResueltoSalida,
    ScriptSalida,
    SolicitudDetalle,
    SolicitudResumida,
    UsuarioSalida,
)
from app.api.scripts import a_salida as script_a_salida
from app.api.scripts import proveedor_para
from app.modulos.generacion import generar_script, registrar_script
from app.modulos.politicas import (
    Resolucion,
    registrar_solicitud,
    resolucion_a_permisos,
    resolver_alta,
    resolver_solicitud,
)
from app.nucleo.enums import EstadoSolicitud
from app.nucleo.modelos import ScriptGenerado, SolicitudAlta, Usuario

router = APIRouter(prefix="/api", tags=["altas"])


# ---------------------------------------------------------------------------
# Usuarios, para elegir sobre quién se hace el alta
# ---------------------------------------------------------------------------


@router.get("/usuarios", response_model=list[UsuarioSalida], tags=["altas"])
def buscar_usuarios(
    q: str = "", limite: int = 20, sesion: Session = Depends(sesion_db)
) -> list[UsuarioSalida]:
    """Busca usuarios por legajo, nombre o puesto."""
    consulta = select(Usuario)
    if q:
        patron = f"%{q.lower()}%"
        consulta = consulta.where(
            func.lower(Usuario.legajo).like(patron)
            | func.lower(Usuario.nombre).like(patron)
            | func.lower(Usuario.puesto).like(patron)
        )
    filas = sesion.scalars(consulta.order_by(Usuario.legajo).limit(limite)).all()
    return [_usuario_salida(u) for u in filas]


def _usuario_salida(u: Usuario) -> UsuarioSalida:
    return UsuarioSalida(
        id=u.id,
        legajo=u.legajo,
        nombre=u.nombre,
        correo=u.correo,
        area=u.area,
        puesto=u.puesto,
        estado=str(u.estado),
        fecha_ingreso=u.fecha_ingreso,
    )


# ---------------------------------------------------------------------------
# Solicitudes
# ---------------------------------------------------------------------------


@router.get("/altas", response_model=list[SolicitudResumida])
def listar_altas(
    estado: str | None = None, sesion: Session = Depends(sesion_db)
) -> list[SolicitudResumida]:
    """Solicitudes de alta, de la más reciente a la más vieja."""
    consulta = select(SolicitudAlta).order_by(SolicitudAlta.id.desc())
    if estado:
        consulta = consulta.where(SolicitudAlta.estado == estado)
    return [_resumida(sesion, s) for s in sesion.scalars(consulta).all()]


def _resumida(sesion: Session, s: SolicitudAlta) -> SolicitudResumida:
    propuestos = s.permisos_propuestos or {}
    conflictos = (s.conflictos_detectados or {}).get("cantidad", 0)
    tiene_script = bool(
        sesion.scalar(
            select(func.count(ScriptGenerado.id)).where(
                ScriptGenerado.solicitud_id == s.id
            )
        )
    )
    return SolicitudResumida(
        id=s.id,
        legajo=s.usuario.legajo,
        nombre=s.usuario.nombre,
        puesto_solicitado=s.puesto_solicitado,
        area_solicitada=s.area_solicitada,
        estado=str(s.estado),
        solicitada_por=s.solicitada_por,
        creada_en=s.creada_en,
        otorgados=len(propuestos.get("otorgados", [])),
        conflictos=conflictos,
        tiene_script=tiene_script,
    )


@router.post("/altas", response_model=SolicitudDetalle, status_code=201)
def crear_alta(
    pedido: PedidoAlta, sesion: Session = Depends(sesion_db)
) -> SolicitudDetalle:
    """Resuelve los accesos de un alta y la deja pendiente de aprobación.

    Lo que decide qué accesos corresponden es el motor de reglas RBAC/ABAC,
    acá y en cualquier otro camino de entrada. Los permisos pedidos a mano
    entran a la propuesta como cualquier otro y pasan por la misma
    validación cruzada de Segregación de Funciones: pedirlos explícitamente
    no saltea el control.
    """
    usuario = sesion.scalars(
        select(Usuario).where(Usuario.legajo == pedido.legajo).limit(1)
    ).one_or_none()
    if usuario is None:
        raise HTTPException(
            status_code=404, detail=f"no existe el legajo {pedido.legajo}"
        )

    resolucion = resolver_alta(
        sesion,
        usuario,
        puesto=pedido.puesto,
        area=pedido.area,
        conservar_vigentes=pedido.conservar_vigentes,
        permisos_solicitados=pedido.permisos_solicitados or None,
    )
    solicitud = registrar_solicitud(
        sesion, usuario, resolucion, solicitada_por=pedido.solicitada_por
    )
    sesion.flush()
    return _detalle(sesion, solicitud, resolucion)


@router.get("/altas/{solicitud_id}", response_model=SolicitudDetalle)
def ver_alta(
    solicitud_id: int, sesion: Session = Depends(sesion_db)
) -> SolicitudDetalle:
    """Detalle de una solicitud, con sus accesos y su script si ya se generó."""
    return _detalle(sesion, _solicitud(sesion, solicitud_id))


def _solicitud(sesion: Session, solicitud_id: int) -> SolicitudAlta:
    solicitud = sesion.get(SolicitudAlta, solicitud_id)
    if solicitud is None:
        raise HTTPException(status_code=404, detail="no existe esa solicitud")
    return solicitud


def _detalle(
    sesion: Session, s: SolicitudAlta, resolucion: Resolucion | None = None
) -> SolicitudDetalle:
    propuestos = s.permisos_propuestos or {}
    excluidos = [
        ExclusionSalida(
            codigo=e["permiso"],
            motivo=e["motivo"],
            detalle=e["detalle"],
            regla=e.get("regla"),
            severidad=e.get("severidad"),
            permiso_en_conflicto=e.get("permiso_en_conflicto"),
        )
        for e in propuestos.get("excluidos", [])
    ]
    en_conflicto = {
        e.permiso_en_conflicto for e in excluidos if e.permiso_en_conflicto
    }
    otorgados = [
        PermisoResueltoSalida(
            codigo=p["permiso"],
            descripcion=p["descripcion"],
            origen=p["origen"],
            rol=p.get("rol"),
            politica=p.get("politica"),
            conflicto=p["permiso"] in en_conflicto,
        )
        for p in propuestos.get("otorgados", [])
    ]

    script = sesion.scalars(
        select(ScriptGenerado)
        .where(ScriptGenerado.solicitud_id == s.id)
        .order_by(ScriptGenerado.id.desc())
        .limit(1)
    ).one_or_none()

    guardada = Resolucion.desde_diccionario(propuestos)
    cambios = resolucion_a_permisos(guardada)

    return SolicitudDetalle(
        id=s.id,
        usuario=_usuario_salida(s.usuario),
        puesto_solicitado=s.puesto_solicitado,
        area_solicitada=s.area_solicitada,
        estado=str(s.estado),
        solicitada_por=s.solicitada_por,
        resuelta_por=s.resuelta_por,
        creada_en=s.creada_en,
        resuelta_en=s.resuelta_en,
        roles=propuestos.get("roles", []),
        perfil_inferido=propuestos.get("perfil_inferido", False),
        otorgados=otorgados,
        excluidos=excluidos,
        conservados=list(propuestos.get("conservados", [])),
        cambios_pendientes=len(cambios),
        script=script_a_salida(script) if script is not None else None,
    )


# ---------------------------------------------------------------------------
# Generación del script
# ---------------------------------------------------------------------------


@router.post("/altas/{solicitud_id}/script", response_model=ScriptSalida, status_code=201)
def generar(
    solicitud_id: int,
    pedido: PedidoGeneracionScript,
    sesion: Session = Depends(sesion_db),
) -> ScriptSalida:
    """Genera el script de aplicación de una solicitud ya resuelta.

    Lo que se le pasa al modelo es el delta que produjo el motor de reglas,
    con la identidad seudonimizada, y nada más. La salida pasa por la cadena
    de controles antes de guardarse; si no la pasa, se devuelve el motivo y
    no queda ningún script aprobado.
    """
    solicitud = _solicitud(sesion, solicitud_id)
    if solicitud.estado != EstadoSolicitud.PENDIENTE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"la solicitud ya fue {solicitud.estado}",
        )

    resolucion = Resolucion.desde_diccionario(solicitud.permisos_propuestos or {})
    cambios = resolucion_a_permisos(resolucion)
    if not cambios:
        raise HTTPException(
            status_code=422, detail="la solicitud no tiene cambios que aplicar"
        )

    proveedor = proveedor_para(pedido.usar_modelo_real)
    resultado = generar_script(
        cambios, solicitud.usuario, plataforma=pedido.plataforma, proveedor=proveedor
    )
    if not resultado.aprobado:
        motivo = resultado.error or (
            resultado.validacion.motivo if resultado.validacion else "sin detalle"
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"la salida del modelo no pasó los controles: {motivo}",
        )

    script = registrar_script(
        sesion,
        resultado,
        actor=pedido.actor,
        solicitud_id=solicitud.id,
        plataforma=pedido.plataforma,
    )
    sesion.flush()
    return script_a_salida(script)


# ---------------------------------------------------------------------------
# Aprobación
# ---------------------------------------------------------------------------


@router.post("/altas/{solicitud_id}/resolver", response_model=SolicitudDetalle)
def resolver(
    solicitud_id: int,
    pedido: PedidoAprobacion,
    sesion: Session = Depends(sesion_db),
) -> SolicitudDetalle:
    """Aprueba o rechaza una solicitud.

    Nadie puede aprobar lo que él mismo originó. Es la misma separación de
    funciones que el sistema detecta en las organizaciones que analiza, y
    dejarla afuera del propio sistema sería una incoherencia.
    """
    solicitud = _solicitud(sesion, solicitud_id)
    try:
        resolver_solicitud(
            sesion, solicitud, aprobada=pedido.aprobar, resuelta_por=pedido.actor
        )
    except PermissionError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(error)
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error

    sesion.flush()
    return _detalle(sesion, solicitud)
