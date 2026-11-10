"""Pruebas del motor de politicas.

Cubren los casos de aceptacion CP-03 (alta sin conflictos), CP-04 (alta con
conflicto de Segregacion de Funciones) y CP-06 (rechazo registrado), y el
indicador I-03, que exige que el 100 % de las altas con conflicto conocido
queden bloqueadas antes del otorgamiento.

La prueba de I-03 es la mas importante del archivo y conviene leerla con
atencion: en lugar de probar unos pocos casos elegidos a mano, recorre
todas las reglas de Segregacion de Funciones del dataset y, para cada una,
construye un alta que pediria explicitamente el permiso incompatible con
algo que la persona ya tiene. Si una sola pasara, el indicador no se
cumple.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modulos.importacion import importar_dataset
from app.modulos.politicas import (
    atributos_de,
    registrar_solicitud,
    resolucion_a_permisos,
    resolver_alta,
    resolver_solicitud,
    resumen,
)
from app.nucleo.enums import EstadoSolicitud
from app.nucleo.modelos import (
    AsignacionAcceso,
    EventoAuditoria,
    Permiso,
    ReglaSoD,
    Usuario,
)
from tests.conftest import FECHA_CORTE


@pytest.fixture()
def cargado(sesion: Session, dataset_d1: dict) -> Session:
    importar_dataset(sesion, dataset_d1["carpeta"])
    sesion.commit()
    return sesion


def _usuario(sesion: Session, puesto: str) -> Usuario:
    usuario = sesion.scalars(
        select(Usuario).where(Usuario.puesto == puesto, Usuario.estado == "activo")
    ).first()
    assert usuario is not None, f"el dataset no tiene nadie en {puesto}"
    return usuario


# ---------------------------------------------------------------------------
# CP-03: alta sin conflictos
# ---------------------------------------------------------------------------


def test_un_alta_limpia_resuelve_accesos_y_no_excluye_nada(cargado: Session) -> None:
    usuario = _usuario(cargado, "Analista contable")
    resolucion = resolver_alta(
        cargado, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
    )

    assert resolucion.otorgados, "un alta tiene que resolver al menos un acceso"
    assert not resolucion.tiene_conflictos
    assert resumen(resolucion)["requiere_atencion"] is False
    assert all(p.origen in ("rbac_rol", "abac_atributo") for p in resolucion.otorgados)


def test_cada_permiso_otorgado_dice_de_donde_sale(cargado: Session) -> None:
    """Sin la trazabilidad del origen, la propuesta no se puede revisar."""
    usuario = _usuario(cargado, "Analista de pagos")
    resolucion = resolver_alta(
        cargado, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
    )
    for permiso in resolucion.otorgados:
        if permiso.origen == "rbac_rol":
            assert permiso.rol, "falta el rol que origina el permiso"
        if permiso.origen == "abac_atributo":
            assert permiso.politica, "falta la politica que origina el permiso"


def test_el_perfil_se_infiere_cuando_el_catalogo_no_lo_declara(
    cargado: Session,
) -> None:
    """El dataset no declara a que puesto apunta cada rol, como pasa en la
    practica. El motor tiene que resolver igual, y decir que lo infirio."""
    usuario = _usuario(cargado, "Analista contable")
    resolucion = resolver_alta(
        cargado, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
    )
    assert resolucion.perfil_inferido is True
    assert resolucion.roles, "el perfil inferido no deberia quedar vacio"


# ---------------------------------------------------------------------------
# CP-04 e I-03: conflictos de Segregacion de Funciones
# ---------------------------------------------------------------------------


def test_un_permiso_incompatible_queda_excluido_y_se_informa_la_regla(
    cargado: Session,
) -> None:
    regla = cargado.scalars(select(ReglaSoD).where(ReglaSoD.activa.is_(True))).first()
    assert regla is not None
    permisos = {p.id: p for p in cargado.scalars(select(Permiso)).all()}
    usuario = _usuario(cargado, "Analista contable")

    resolucion = resolver_alta(
        cargado,
        usuario,
        fecha_corte=FECHA_CORTE,
        conservar_vigentes=False,
        permisos_solicitados=[
            permisos[regla.permiso_a_id].codigo,
            permisos[regla.permiso_b_id].codigo,
        ],
    )

    assert resolucion.tiene_conflictos
    conflicto = next(e for e in resolucion.excluidos if e.motivo == "conflicto_sod")
    assert conflicto.regla == regla.codigo
    assert conflicto.permiso_en_conflicto
    assert conflicto.severidad

    otorgados = {p.codigo for p in resolucion.otorgados}
    assert not (
        permisos[regla.permiso_a_id].codigo in otorgados
        and permisos[regla.permiso_b_id].codigo in otorgados
    ), "las dos mitades de la regla no pueden quedar otorgadas"


def test_i03_todas_las_altas_con_conflicto_conocido_se_bloquean(
    cargado: Session,
) -> None:
    """Indicador I-03: el bloqueo tiene que ser del 100 % de los casos.

    Para cada regla se busca a alguien que ya tenga una de las dos mitades y
    se pide la otra. Es el caso que mas se parece a lo que pasa en una
    organizacion: el acceso nuevo parece inofensivo, y lo que lo vuelve
    peligroso es algo que la persona ya tenia.
    """
    permisos = {p.id: p for p in cargado.scalars(select(Permiso)).all()}
    reglas = cargado.scalars(select(ReglaSoD).where(ReglaSoD.activa.is_(True))).all()

    tenedores: dict[int, int] = {}
    for asignacion in cargado.scalars(
        select(AsignacionAcceso).where(AsignacionAcceso.activa.is_(True))
    ).all():
        tenedores.setdefault(asignacion.permiso_id, asignacion.usuario_id)

    evaluados = 0
    bloqueados = 0
    for regla in reglas:
        for tiene, pide in (
            (regla.permiso_a_id, regla.permiso_b_id),
            (regla.permiso_b_id, regla.permiso_a_id),
        ):
            usuario_id = tenedores.get(tiene)
            if usuario_id is None:
                continue
            usuario = cargado.get(Usuario, usuario_id)
            assert usuario is not None
            resolucion = resolver_alta(
                cargado,
                usuario,
                fecha_corte=FECHA_CORTE,
                permisos_solicitados=[permisos[pide].codigo],
            )
            evaluados += 1
            otorgados = {p.codigo for p in resolucion.otorgados}
            if permisos[pide].codigo not in otorgados:
                bloqueados += 1
            break

    assert evaluados >= len(reglas) // 2, "la muestra deberia cubrir las reglas"
    assert bloqueados == evaluados, (
        f"se bloquearon {bloqueados} de {evaluados} altas con conflicto conocido"
    )


def test_la_validacion_cruza_contra_lo_que_la_persona_ya_tiene(
    cargado: Session,
) -> None:
    """Es la diferencia entre este motor y un formulario de alta.

    El mismo pedido pasa cuando se evalua aislado y se bloquea cuando se lo
    cruza con los accesos vigentes de la persona.
    """
    permisos = {p.id: p for p in cargado.scalars(select(Permiso)).all()}
    por_codigo = {p.codigo: p for p in permisos.values()}

    caso = None
    for regla in cargado.scalars(select(ReglaSoD).where(ReglaSoD.activa.is_(True))):
        asignacion = cargado.scalars(
            select(AsignacionAcceso).where(
                AsignacionAcceso.permiso_id == regla.permiso_a_id,
                AsignacionAcceso.activa.is_(True),
            )
        ).first()
        if asignacion is None:
            continue
        usuario = cargado.get(Usuario, asignacion.usuario_id)
        vigentes = {
            a.permiso_id
            for a in cargado.scalars(
                select(AsignacionAcceso).where(
                    AsignacionAcceso.usuario_id == asignacion.usuario_id,
                    AsignacionAcceso.activa.is_(True),
                )
            )
        }
        if regla.permiso_b_id in vigentes:
            continue
        caso = (usuario, permisos[regla.permiso_b_id].codigo)
        break

    assert caso is not None, "el dataset deberia ofrecer un caso asi"
    usuario, pedido = caso

    aislado = resolver_alta(
        cargado,
        usuario,
        fecha_corte=FECHA_CORTE,
        conservar_vigentes=False,
        permisos_solicitados=[pedido],
    )
    cruzado = resolver_alta(
        cargado,
        usuario,
        fecha_corte=FECHA_CORTE,
        conservar_vigentes=True,
        permisos_solicitados=[pedido],
    )

    assert pedido in {p.codigo for p in aislado.otorgados}
    assert pedido not in {p.codigo for p in cruzado.otorgados}
    assert por_codigo[pedido]


def test_un_permiso_solicitado_que_no_existe_se_informa(cargado: Session) -> None:
    usuario = _usuario(cargado, "Analista contable")
    resolucion = resolver_alta(
        cargado,
        usuario,
        fecha_corte=FECHA_CORTE,
        conservar_vigentes=False,
        permisos_solicitados=["NO_EXISTE"],
    )
    assert any(e.motivo == "permiso_inexistente" for e in resolucion.excluidos)


# ---------------------------------------------------------------------------
# Politicas de atributos
# ---------------------------------------------------------------------------


def test_una_politica_de_atributos_niega_el_permiso_fuera_de_su_area(
    cargado: Session,
) -> None:
    """La administracion de identidades esta restringida a Tecnologia."""
    usuario = _usuario(cargado, "Analista contable")
    admin = cargado.scalars(
        select(Permiso).where(Permiso.codigo.like("%ADMIN_USUARIOS"))
    ).first()
    assert admin is not None

    resolucion = resolver_alta(
        cargado,
        usuario,
        fecha_corte=FECHA_CORTE,
        conservar_vigentes=False,
        permisos_solicitados=[admin.codigo],
    )
    exclusion = next(e for e in resolucion.excluidos if e.codigo == admin.codigo)
    assert exclusion.motivo == "politica_abac"
    assert "area" in exclusion.detalle


def test_la_misma_politica_deja_pasar_a_quien_si_cumple(cargado: Session) -> None:
    usuario = _usuario(cargado, "Administrador de sistemas")
    admin = cargado.scalars(
        select(Permiso).where(Permiso.codigo.like("DIR_ADMIN_USUARIOS"))
    ).first()
    assert admin is not None

    resolucion = resolver_alta(
        cargado,
        usuario,
        fecha_corte=FECHA_CORTE,
        conservar_vigentes=False,
        permisos_solicitados=[admin.codigo],
    )
    assert admin.codigo in {p.codigo for p in resolucion.otorgados}


def test_los_atributos_incluyen_la_antiguedad(cargado: Session) -> None:
    usuario = _usuario(cargado, "Analista contable")
    atributos = atributos_de(usuario, FECHA_CORTE)
    assert atributos["antiguedad_dias"] > 0
    assert atributos["area"] == usuario.area


# ---------------------------------------------------------------------------
# CP-06: circuito de aprobacion
# ---------------------------------------------------------------------------


def test_la_solicitud_queda_registrada_con_sus_conflictos(cargado: Session) -> None:
    usuario = _usuario(cargado, "Analista contable")
    regla = cargado.scalars(select(ReglaSoD).where(ReglaSoD.activa.is_(True))).first()
    permisos = {p.id: p for p in cargado.scalars(select(Permiso)).all()}
    resolucion = resolver_alta(
        cargado,
        usuario,
        fecha_corte=FECHA_CORTE,
        conservar_vigentes=False,
        permisos_solicitados=[
            permisos[regla.permiso_a_id].codigo,
            permisos[regla.permiso_b_id].codigo,
        ],
    )
    solicitud = registrar_solicitud(cargado, usuario, resolucion, solicitada_por="U00002")
    cargado.commit()

    assert solicitud.estado == EstadoSolicitud.PENDIENTE
    assert solicitud.conflictos_detectados["cantidad"] >= 1
    assert regla.codigo in solicitud.conflictos_detectados["reglas"]
    assert solicitud.permisos_propuestos["otorgados"]


def test_nadie_aprueba_su_propia_solicitud(cargado: Session) -> None:
    """Separacion de funciones aplicada al propio sistema.

    Seria incoherente que una plataforma cuyo tema es la segregacion de
    funciones permitiera que la misma persona origine y apruebe un acceso.
    """
    usuario = _usuario(cargado, "Analista contable")
    resolucion = resolver_alta(
        cargado, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
    )
    solicitud = registrar_solicitud(cargado, usuario, resolucion, solicitada_por="U00002")

    with pytest.raises(PermissionError, match="no puede aprobarla"):
        resolver_solicitud(cargado, solicitud, aprobada=True, resuelta_por="U00002")

    assert solicitud.estado == EstadoSolicitud.PENDIENTE


def test_un_rechazo_no_aplica_cambios_y_queda_auditado(cargado: Session) -> None:
    usuario = _usuario(cargado, "Analista contable")
    antes = cargado.scalars(
        select(AsignacionAcceso).where(AsignacionAcceso.usuario_id == usuario.id)
    ).all()

    resolucion = resolver_alta(
        cargado, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
    )
    solicitud = registrar_solicitud(cargado, usuario, resolucion, solicitada_por="U00002")
    resolver_solicitud(cargado, solicitud, aprobada=False, resuelta_por="U00003")
    cargado.commit()

    despues = cargado.scalars(
        select(AsignacionAcceso).where(AsignacionAcceso.usuario_id == usuario.id)
    ).all()
    assert len(antes) == len(despues), "un rechazo no debe tocar los accesos"
    assert solicitud.estado == EstadoSolicitud.RECHAZADA
    assert solicitud.resuelta_por == "U00003"

    evento = cargado.scalars(
        select(EventoAuditoria).where(EventoAuditoria.accion == "resolver_solicitud")
    ).one()
    assert evento.detalle["estado"] == EstadoSolicitud.RECHAZADA


def test_una_solicitud_resuelta_no_se_vuelve_a_resolver(cargado: Session) -> None:
    usuario = _usuario(cargado, "Analista contable")
    resolucion = resolver_alta(
        cargado, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
    )
    solicitud = registrar_solicitud(cargado, usuario, resolucion, solicitada_por="U00002")
    resolver_solicitud(cargado, solicitud, aprobada=True, resuelta_por="U00003")

    with pytest.raises(ValueError, match="ya fue"):
        resolver_solicitud(cargado, solicitud, aprobada=False, resuelta_por="U00004")


def test_la_resolucion_se_convierte_en_asignaciones(cargado: Session) -> None:
    """Lo aprobado tiene que poder aplicarse sobre el modelo de datos."""
    usuario = _usuario(cargado, "Analista contable")
    resolucion = resolver_alta(
        cargado, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
    )
    solicitud = registrar_solicitud(cargado, usuario, resolucion, solicitada_por="U00002")
    resolver_solicitud(cargado, solicitud, aprobada=True, resuelta_por="U00003")

    nuevas = resolucion_a_permisos(resolucion)
    codigos = {p.codigo for p in resolucion.otorgados}
    assert {n.permiso_codigo for n in nuevas} <= codigos
