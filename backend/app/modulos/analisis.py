"""Modulo de analisis: las cinco reglas de deteccion.

Cada regla responde a una pregunta distinta sobre el mismo conjunto de
accesos, y las cinco comparten la forma de la salida: un hallazgo con su
categoria, su severidad, un puntaje de riesgo y la evidencia que permite
entender por que se levanto, sin tener que volver a los datos crudos.

Dos decisiones de diseno vale explicitarlas, porque condicionan los
resultados:

El analisis se hace en memoria. Se traen las tablas con unas pocas
consultas y el calculo corre sobre estructuras de Python y un dataframe de
pandas, en lugar de resolverse con consultas por usuario. Con cinco mil
identidades y cincuenta mil asignaciones la diferencia entre las dos formas
es de dos ordenes de magnitud, y el requisito de tiempo del plan de pruebas
es de sesenta segundos para ese volumen.

El sobreprivilegio se mide contra la mediana del puesto y no contra el
percentil. En un puesto con pocas personas, dos o tres casos atipicos
corren el percentil hacia arriba y terminan definiendo el umbral que
deberia delatarlos; la mediana no se mueve por eso. Es un cambio respecto
de la formulacion inicial, y la razon es empirica: con el percentil, parte
de los casos plantados en el conjunto de prueba quedaban por debajo de su
propio umbral.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nucleo.config import obtener_config
from app.nucleo.enums import (
    PESO_SEVERIDAD,
    Categoria,
    EstadoUsuario,
    OrigenAsignacion,
    Severidad,
)
from app.nucleo.modelos import (
    AsignacionAcceso,
    EventoAuditoria,
    Hallazgo,
    Permiso,
    ReglaSoD,
    RolPermiso,
    Usuario,
)

# Umbrales de severidad segun el peso del permiso mas poderoso involucrado.
UMBRAL_CRITICA = 9
UMBRAL_ALTA = 6
UMBRAL_MEDIA = 3

# Un puesto con menos integrantes que esto no da una mediana representativa,
# asi que no se evalua sobreprivilegio sobre el.
MINIMO_PARES = 5


@dataclass
class ParametrosAnalisis:
    """Lo que se puede ajustar sin tocar el codigo de las reglas."""

    fecha_corte: date
    dias_cuenta_dormida: int = 90
    factor_sobre_privilegio: float = 2.0
    antiguedad_creep_dias: int = 365
    minimo_pares: int = MINIMO_PARES

    @classmethod
    def desde_configuracion(cls, fecha_corte: date) -> ParametrosAnalisis:
        """Toma los umbrales del entorno, que es como corre en produccion."""
        config = obtener_config()
        return cls(
            fecha_corte=fecha_corte,
            dias_cuenta_dormida=config.dias_cuenta_dormida,
            factor_sobre_privilegio=config.factor_sobreprivilegio,
            antiguedad_creep_dias=config.antiguedad_creep_dias,
            minimo_pares=config.minimo_pares_sobreprivilegio,
        )


@dataclass
class _Contexto:
    """Los datos del snapshot ya cargados, indexados como los usan las reglas."""

    usuarios: dict[int, Usuario]
    permisos: dict[int, Permiso]
    activas_por_usuario: dict[int, list[AsignacionAcceso]] = field(
        default_factory=lambda: defaultdict(list)
    )
    permisos_de_rol: dict[int, set[int]] = field(default_factory=dict)
    reglas: list[ReglaSoD] = field(default_factory=list)


def severidad_por_peso(peso: int) -> Severidad:
    """Traduce el poder del permiso involucrado a una severidad."""
    if peso >= UMBRAL_CRITICA:
        return Severidad.CRITICA
    if peso >= UMBRAL_ALTA:
        return Severidad.ALTA
    if peso >= UMBRAL_MEDIA:
        return Severidad.MEDIA
    return Severidad.BAJA


def puntaje(severidad: Severidad, pesos: list[int]) -> float:
    """Indice de riesgo del hallazgo.

    Es el producto de dos cosas que se leen por separado: cuanto importa el
    tipo de desviacion, dado por el peso de la severidad, y cuanto poder
    otorga concretamente lo que esta mal, dado por la suma de los pesos de
    los permisos involucrados. Deliberadamente no se normaliza: el puntaje
    sirve para ordenar la cola de trabajo del analista, no para compararse
    contra un valor absoluto.
    """
    return float(PESO_SEVERIDAD[severidad] * sum(pesos))


# ---------------------------------------------------------------------------
# Carga del contexto
# ---------------------------------------------------------------------------


def _cargar_contexto(sesion: Session) -> _Contexto:
    usuarios = {u.id: u for u in sesion.scalars(select(Usuario)).all()}
    permisos = {p.id: p for p in sesion.scalars(select(Permiso)).all()}

    contexto = _Contexto(usuarios=usuarios, permisos=permisos)
    contexto.activas_por_usuario = defaultdict(list)
    for asignacion in sesion.scalars(
        select(AsignacionAcceso).where(AsignacionAcceso.activa.is_(True))
    ).all():
        contexto.activas_por_usuario[asignacion.usuario_id].append(asignacion)

    permisos_de_rol: dict[int, set[int]] = defaultdict(set)
    for rp in sesion.scalars(select(RolPermiso)).all():
        permisos_de_rol[rp.rol_id].add(rp.permiso_id)
    contexto.permisos_de_rol = permisos_de_rol

    contexto.reglas = list(
        sesion.scalars(select(ReglaSoD).where(ReglaSoD.activa.is_(True))).all()
    )
    return contexto


def _pesos(contexto: _Contexto, asignaciones: list[AsignacionAcceso]) -> list[int]:
    return [contexto.permisos[a.permiso_id].peso_riesgo for a in asignaciones]


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------


def _cuentas_dormidas(
    contexto: _Contexto, p: ParametrosAnalisis, snapshot_id: int
) -> list[Hallazgo]:
    """Identidades vigentes que hace demasiado que no se usan.

    Un acceso que no se ejerce sigue siendo un acceso: no reduce la
    superficie de exposicion, solo la vuelve menos visible. La ausencia de
    fecha de ultimo uso se trata como dormida y no como dato faltante,
    porque en los sistemas que registran el uso esa ausencia significa que
    nunca se entro.
    """
    hallazgos = []
    for usuario in contexto.usuarios.values():
        if usuario.estado != EstadoUsuario.ACTIVO:
            continue
        activas = contexto.activas_por_usuario.get(usuario.id, [])
        if not activas:
            continue

        if usuario.ultimo_acceso is None:
            dias = None
        else:
            dias = (p.fecha_corte - usuario.ultimo_acceso).days
            if dias <= p.dias_cuenta_dormida:
                continue

        pesos = _pesos(contexto, activas)
        severidad = severidad_por_peso(max(pesos))
        hallazgos.append(
            Hallazgo(
                snapshot_id=snapshot_id,
                usuario_id=usuario.id,
                categoria=Categoria.CUENTA_DORMIDA,
                severidad=severidad,
                puntaje=puntaje(severidad, pesos),
                evidencia={
                    "legajo": usuario.legajo,
                    "ultimo_acceso": (
                        usuario.ultimo_acceso.isoformat()
                        if usuario.ultimo_acceso
                        else None
                    ),
                    "dias_sin_uso": dias,
                    "umbral_dias": p.dias_cuenta_dormida,
                    "permisos_activos": len(activas),
                    "peso_maximo": max(pesos),
                },
                recomendacion=(
                    "Confirmar con el responsable del area si la persona sigue "
                    "necesitando el acceso. Si no, revocar las asignaciones "
                    "activas y dejar la cuenta deshabilitada."
                ),
            )
        )
    return hallazgos


def _cuentas_huerfanas(
    contexto: _Contexto, p: ParametrosAnalisis, snapshot_id: int
) -> list[Hallazgo]:
    """Personas dadas de baja que conservan accesos vivos.

    Es el hallazgo mas grave de los cinco en terminos de exposicion, porque
    el acceso sobrevive al vinculo que lo justificaba y nadie lo esta
    mirando. Por eso la severidad arranca un escalon mas arriba de lo que
    indicaria el peso de los permisos.
    """
    hallazgos = []
    for usuario in contexto.usuarios.values():
        if usuario.estado == EstadoUsuario.ACTIVO:
            continue
        activas = contexto.activas_por_usuario.get(usuario.id, [])
        if not activas:
            continue

        pesos = _pesos(contexto, activas)
        severidad = _escalar(severidad_por_peso(max(pesos)))
        dias = (
            (p.fecha_corte - usuario.fecha_baja).days if usuario.fecha_baja else None
        )
        hallazgos.append(
            Hallazgo(
                snapshot_id=snapshot_id,
                usuario_id=usuario.id,
                categoria=Categoria.CUENTA_HUERFANA,
                severidad=severidad,
                puntaje=puntaje(severidad, pesos),
                evidencia={
                    "legajo": usuario.legajo,
                    "estado": usuario.estado,
                    "fecha_baja": (
                        usuario.fecha_baja.isoformat() if usuario.fecha_baja else None
                    ),
                    "dias_desde_la_baja": dias,
                    "permisos_activos": len(activas),
                    "aplicaciones": sorted(
                        {
                            contexto.permisos[a.permiso_id].aplicacion_id
                            for a in activas
                        }
                    ),
                },
                recomendacion=(
                    "Revocar de inmediato todas las asignaciones activas y "
                    "verificar en el sistema destino que la cuenta quede "
                    "deshabilitada, no solo sin permisos."
                ),
            )
        )
    return hallazgos


def _escalar(severidad: Severidad) -> Severidad:
    """Sube un escalon de severidad, sin pasarse de critica."""
    orden = [Severidad.BAJA, Severidad.MEDIA, Severidad.ALTA, Severidad.CRITICA]
    return orden[min(orden.index(severidad) + 1, len(orden) - 1)]


def _sobre_privilegio(
    contexto: _Contexto, p: ParametrosAnalisis, snapshot_id: int
) -> list[Hallazgo]:
    """Acumulacion de permisos muy por encima de los pares del mismo puesto.

    El criterio es relativo porque no existe un numero de permisos que sea
    correcto en abstracto: lo que distingue a un caso es tener mucho mas que
    quienes hacen el mismo trabajo. Los puestos con pocos integrantes se
    excluyen, porque una mediana calculada sobre tres personas no describe
    nada.
    """
    filas = [
        {
            "usuario_id": usuario.id,
            "puesto": usuario.puesto,
            "permisos": len(contexto.activas_por_usuario.get(usuario.id, [])),
        }
        for usuario in contexto.usuarios.values()
        if usuario.estado == EstadoUsuario.ACTIVO
    ]
    if not filas:
        return []

    df = pd.DataFrame(filas)
    agrupado = df.groupby("puesto")["permisos"].agg(["median", "size"])
    df = df.join(agrupado, on="puesto")
    df["umbral"] = df["median"] * p.factor_sobre_privilegio
    marcados = df[(df["size"] >= p.minimo_pares) & (df["permisos"] >= df["umbral"])]

    hallazgos = []
    for fila in marcados.itertuples():
        usuario = contexto.usuarios[fila.usuario_id]
        activas = contexto.activas_por_usuario[usuario.id]
        pesos = _pesos(contexto, activas)
        severidad = severidad_por_peso(max(pesos))
        hallazgos.append(
            Hallazgo(
                snapshot_id=snapshot_id,
                usuario_id=usuario.id,
                categoria=Categoria.SOBRE_PRIVILEGIO,
                severidad=severidad,
                puntaje=puntaje(severidad, pesos),
                evidencia={
                    "legajo": usuario.legajo,
                    "puesto": usuario.puesto,
                    "permisos": int(fila.permisos),
                    "mediana_del_puesto": float(fila.median),
                    "umbral": float(fila.umbral),
                    "pares_evaluados": int(fila.size),
                    "peso_total": sum(pesos),
                },
                recomendacion=(
                    "Comparar los accesos con los del rol del puesto y revocar "
                    "lo que no corresponda a la funcion actual. Si la "
                    "diferencia esta justificada, documentar la excepcion."
                ),
            )
        )
    return hallazgos


def _conflictos_sod(
    contexto: _Contexto, p: ParametrosAnalisis, snapshot_id: int
) -> list[Hallazgo]:
    """Identidades que concentran dos funciones incompatibles entre si.

    Se genera un hallazgo por cada par en conflicto y no uno por usuario:
    cada regla infringida se remedia por separado, y agrupar perderia cual
    es la que hay que resolver.
    """
    hallazgos = []
    for usuario_id, activas in contexto.activas_por_usuario.items():
        if usuario_id not in contexto.usuarios:
            continue
        por_permiso = {a.permiso_id: a for a in activas}
        for regla in contexto.reglas:
            if regla.permiso_a_id not in por_permiso:
                continue
            if regla.permiso_b_id not in por_permiso:
                continue
            usuario = contexto.usuarios[usuario_id]
            pa = contexto.permisos[regla.permiso_a_id]
            pb = contexto.permisos[regla.permiso_b_id]
            severidad = Severidad(regla.severidad)
            hallazgos.append(
                Hallazgo(
                    snapshot_id=snapshot_id,
                    usuario_id=usuario.id,
                    regla_sod_id=regla.id,
                    categoria=Categoria.CONFLICTO_SOD,
                    severidad=severidad,
                    puntaje=puntaje(severidad, [pa.peso_riesgo, pb.peso_riesgo]),
                    evidencia={
                        "legajo": usuario.legajo,
                        "regla": regla.codigo,
                        "descripcion": regla.descripcion,
                        "permisos": [pa.codigo, pb.codigo],
                        "origenes": [
                            por_permiso[regla.permiso_a_id].origen,
                            por_permiso[regla.permiso_b_id].origen,
                        ],
                        "fundamento": regla.fundamento,
                    },
                    recomendacion=(
                        f"Revocar uno de los dos permisos de la regla "
                        f"{regla.codigo}. Si la funcion requiere ambos, "
                        "documentar un control compensatorio y la aprobacion "
                        "del dueno de la aplicacion."
                    ),
                )
            )
    return hallazgos


def _privilege_creep(
    contexto: _Contexto, p: ParametrosAnalisis, snapshot_id: int
) -> list[Hallazgo]:
    """Accesos arrastrados de funciones anteriores.

    El rastro de un cambio de puesto sin revocacion tiene tres marcas
    juntas: la asignacion es directa y no viene de un rol, es vieja, y
    nunca fue revisada. Ninguna de las tres por separado es sospechosa; las
    tres juntas, si.
    """
    hallazgos = []
    for usuario_id, activas in contexto.activas_por_usuario.items():
        if usuario_id not in contexto.usuarios:
            continue
        usuario = contexto.usuarios[usuario_id]

        propios: set[int] = set()
        for asignacion in activas:
            if asignacion.origen == OrigenAsignacion.RBAC_ROL and asignacion.rol_id:
                propios |= contexto.permisos_de_rol.get(asignacion.rol_id, set())

        heredados = [
            a
            for a in activas
            if a.origen == OrigenAsignacion.DIRECTA
            and a.fecha_revision is None
            and (p.fecha_corte - a.fecha_otorgamiento).days > p.antiguedad_creep_dias
            and a.permiso_id not in propios
        ]
        if not heredados:
            continue

        pesos = _pesos(contexto, heredados)
        severidad = severidad_por_peso(max(pesos))
        hallazgos.append(
            Hallazgo(
                snapshot_id=snapshot_id,
                usuario_id=usuario.id,
                categoria=Categoria.PRIVILEGE_CREEP,
                severidad=severidad,
                puntaje=puntaje(severidad, pesos),
                evidencia={
                    "legajo": usuario.legajo,
                    "puesto_actual": usuario.puesto,
                    "permisos": [
                        contexto.permisos[a.permiso_id].codigo for a in heredados
                    ],
                    "antiguedad_maxima_dias": max(
                        (p.fecha_corte - a.fecha_otorgamiento).days for a in heredados
                    ),
                    "umbral_dias": p.antiguedad_creep_dias,
                },
                recomendacion=(
                    "Revisar con el jefe directo si los accesos siguen siendo "
                    "necesarios en el puesto actual. Lo que quede, pasarlo al "
                    "rol correspondiente en lugar de dejarlo como otorgamiento "
                    "directo."
                ),
            )
        )
    return hallazgos


REGLAS = (
    _cuentas_dormidas,
    _cuentas_huerfanas,
    _sobre_privilegio,
    _conflictos_sod,
    _privilege_creep,
)


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------


def analizar(
    sesion: Session,
    snapshot_id: int,
    parametros: ParametrosAnalisis,
    actor: str = "sistema",
) -> list[Hallazgo]:
    """Corre las cinco reglas y persiste los hallazgos del snapshot."""
    contexto = _cargar_contexto(sesion)
    hallazgos: list[Hallazgo] = []
    for regla in REGLAS:
        hallazgos.extend(regla(contexto, parametros, snapshot_id))

    sesion.add_all(hallazgos)
    sesion.add(
        EventoAuditoria(
            actor=actor,
            accion="analizar_snapshot",
            entidad="snapshot",
            entidad_id=snapshot_id,
            detalle={
                "hallazgos": len(hallazgos),
                "parametros": {
                    "fecha_corte": parametros.fecha_corte.isoformat(),
                    "dias_cuenta_dormida": parametros.dias_cuenta_dormida,
                    "factor_sobre_privilegio": parametros.factor_sobre_privilegio,
                    "antiguedad_creep_dias": parametros.antiguedad_creep_dias,
                },
            },
        )
    )
    sesion.flush()
    return hallazgos


def resumen(hallazgos: list[Hallazgo], usuarios: int = 0) -> dict[str, Any]:
    """Agregados que consume el panel de diagnostico."""
    por_categoria: dict[str, int] = defaultdict(int)
    por_severidad: dict[str, int] = defaultdict(int)
    total = 0.0
    for h in hallazgos:
        por_categoria[str(h.categoria)] += 1
        por_severidad[str(h.severidad)] += 1
        total += float(h.puntaje)

    return {
        "hallazgos": len(hallazgos),
        "por_categoria": dict(por_categoria),
        "por_severidad": dict(por_severidad),
        "riesgo_total": round(total, 2),
        "riesgo_por_usuario": round(total / usuarios, 2) if usuarios else None,
    }
