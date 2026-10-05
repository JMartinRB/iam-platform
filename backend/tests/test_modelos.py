"""Pruebas del modelo de datos.

Lo que se verifica aca no es que SQLAlchemy sepa insertar filas, sino que la
base rechace por si misma los estados que el dominio no admite. La razon es
concreta: la plataforma va a recibir datos de exports y de scripts, no solo
de sus propios formularios, y una restriccion que vive unicamente en el
codigo de la aplicacion no protege nada cuando la escritura entra por otro
camino.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.nucleo import modelos as m
from app.nucleo.enums import Categoria, Criticidad, NivelPrivilegio, Severidad

HOY = date(2026, 10, 1)


# ---------------------------------------------------------------------------
# Altas minimas reutilizables
# ---------------------------------------------------------------------------


def alta_aplicacion(s: Session, codigo: str = "ERP") -> m.Aplicacion:
    app = m.Aplicacion(
        codigo=codigo,
        nombre="ERP corporativo",
        criticidad=Criticidad.ALTA,
        propietario="Responsable ERP",
        tecnologia="SAP",
    )
    s.add(app)
    s.flush()
    return app


def alta_permiso(
    s: Session, app: m.Aplicacion, codigo: str, peso: int = 5
) -> m.Permiso:
    permiso = m.Permiso(
        aplicacion_id=app.id,
        codigo=codigo,
        descripcion=codigo.replace("_", " ").lower(),
        nivel=NivelPrivilegio.ESCRITURA,
        peso_riesgo=peso,
    )
    s.add(permiso)
    s.flush()
    return permiso


def alta_usuario(s: Session, legajo: str = "U00001") -> m.Usuario:
    usuario = m.Usuario(
        legajo=legajo,
        nombre="Lucia Gomez",
        correo=f"{legajo.lower()}@empresa-demo.test",
        area="Finanzas",
        puesto="Analista contable",
        estado="activo",
        fecha_ingreso=date(2022, 3, 1),
    )
    s.add(usuario)
    s.flush()
    return usuario


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------


def test_el_esquema_tiene_las_trece_entidades(sesion: Session) -> None:
    esperadas = {
        "aplicacion",
        "permiso",
        "rol",
        "rol_permiso",
        "usuario",
        "asignacion_acceso",
        "regla_sod",
        "politica_abac",
        "snapshot",
        "hallazgo",
        "solicitud_alta",
        "script_generado",
        "evento_auditoria",
    }
    assert set(m.Base.metadata.tables) == esperadas


def test_alta_completa_de_un_acceso(sesion: Session) -> None:
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CARGA_PAGOS")
    usuario = alta_usuario(sesion)
    rol = m.Rol(aplicacion_id=app.id, codigo="ERP_OPERATIVO", nombre="Operativo de ERP")
    sesion.add(rol)
    sesion.flush()
    sesion.add(m.RolPermiso(rol_id=rol.id, permiso_id=permiso.id))
    sesion.add(
        m.AsignacionAcceso(
            usuario_id=usuario.id,
            permiso_id=permiso.id,
            rol_id=rol.id,
            origen="rbac_rol",
            fecha_otorgamiento=HOY,
        )
    )
    sesion.commit()
    assert sesion.query(m.AsignacionAcceso).count() == 1
    assert sesion.query(m.AsignacionAcceso).one().activa is True


# ---------------------------------------------------------------------------
# Unicidad
# ---------------------------------------------------------------------------


def test_un_usuario_no_puede_tener_dos_veces_el_mismo_permiso(
    sesion: Session,
) -> None:
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CONSULTA")
    usuario = alta_usuario(sesion)
    for _ in range(2):
        sesion.add(
            m.AsignacionAcceso(
                usuario_id=usuario.id,
                permiso_id=permiso.id,
                origen="directa",
                fecha_otorgamiento=HOY,
            )
        )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_el_codigo_de_permiso_es_unico_por_aplicacion(sesion: Session) -> None:
    app = alta_aplicacion(sesion)
    alta_permiso(sesion, app, "ERP_CONSULTA")
    otra = alta_aplicacion(sesion, "CRM")
    # el mismo sufijo en otra aplicacion es valido
    alta_permiso(sesion, otra, "CRM_CONSULTA")
    sesion.add(
        m.Permiso(
            aplicacion_id=app.id,
            codigo="ERP_CONSULTA",
            descripcion="duplicado",
            nivel=NivelPrivilegio.LECTURA,
            peso_riesgo=1,
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_el_legajo_es_unico(sesion: Session) -> None:
    alta_usuario(sesion, "U00001")
    with pytest.raises(IntegrityError):
        alta_usuario(sesion, "U00001")


# ---------------------------------------------------------------------------
# Dominios cerrados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("criticidad", "critica"),
        ("criticidad", ""),
    ],
)
def test_la_criticidad_fuera_del_dominio_se_rechaza(
    sesion: Session, campo: str, valor: str
) -> None:
    sesion.add(
        m.Aplicacion(
            codigo="X",
            nombre="X",
            propietario="X",
            **{campo: valor},
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_el_estado_de_usuario_fuera_del_dominio_se_rechaza(sesion: Session) -> None:
    usuario = alta_usuario(sesion)
    usuario.estado = "suspendido"
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_el_origen_de_asignacion_fuera_del_dominio_se_rechaza(
    sesion: Session,
) -> None:
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CONSULTA")
    usuario = alta_usuario(sesion)
    sesion.add(
        m.AsignacionAcceso(
            usuario_id=usuario.id,
            permiso_id=permiso.id,
            origen="transferencia",
            fecha_otorgamiento=HOY,
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_una_asignacion_por_rol_exige_el_rol(sesion: Session) -> None:
    """Sin esta restriccion se perderia la trazabilidad del otorgamiento.

    Una asignacion que dice venir de un rol pero no indica cual no se puede
    auditar ni revocar junto con el rol, que es justamente para lo que sirve
    el modelo RBAC.
    """
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CONSULTA")
    usuario = alta_usuario(sesion)
    sesion.add(
        m.AsignacionAcceso(
            usuario_id=usuario.id,
            permiso_id=permiso.id,
            origen="rbac_rol",
            rol_id=None,
            fecha_otorgamiento=HOY,
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_el_peso_de_riesgo_esta_acotado(sesion: Session) -> None:
    app = alta_aplicacion(sesion)
    with pytest.raises(IntegrityError):
        alta_permiso(sesion, app, "ERP_CONSULTA", peso=11)


# ---------------------------------------------------------------------------
# Politicas
# ---------------------------------------------------------------------------


def test_una_regla_sod_no_puede_enfrentar_un_permiso_consigo_mismo(
    sesion: Session,
) -> None:
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CARGA_PAGOS")
    sesion.add(
        m.ReglaSoD(
            codigo="SOD-001",
            descripcion="invalida",
            permiso_a_id=permiso.id,
            permiso_b_id=permiso.id,
            severidad=Severidad.CRITICA,
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_la_politica_abac_guarda_la_condicion_como_json(sesion: Session) -> None:
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CIERRE_CONTABLE")
    sesion.add(
        m.PoliticaABAC(
            codigo="ABAC-001",
            descripcion="Solo Finanzas puede cerrar el periodo",
            permiso_id=permiso.id,
            condicion={"area": ["Finanzas"], "estado": ["activo"]},
            efecto="permitir",
            prioridad=10,
        )
    )
    sesion.commit()
    politica = sesion.query(m.PoliticaABAC).one()
    assert politica.condicion["area"] == ["Finanzas"]


def test_el_efecto_de_la_politica_abac_esta_acotado(sesion: Session) -> None:
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CONSULTA")
    sesion.add(
        m.PoliticaABAC(
            codigo="ABAC-002",
            descripcion="invalida",
            permiso_id=permiso.id,
            condicion={},
            efecto="quizas",
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


# ---------------------------------------------------------------------------
# Operacion
# ---------------------------------------------------------------------------


def test_el_hallazgo_conserva_su_evidencia(sesion: Session) -> None:
    app = alta_aplicacion(sesion)
    permiso = alta_permiso(sesion, app, "ERP_CONSULTA")
    usuario = alta_usuario(sesion)
    snapshot = m.Snapshot(etiqueta="D-2", filas_leidas=20_000, filas_descartadas=5)
    sesion.add(snapshot)
    sesion.flush()
    sesion.add(
        m.Hallazgo(
            snapshot_id=snapshot.id,
            usuario_id=usuario.id,
            permiso_id=permiso.id,
            categoria=Categoria.CUENTA_DORMIDA,
            severidad=Severidad.ALTA,
            puntaje=42.5,
            evidencia={"dias_sin_uso": 215, "ultimo_acceso": "2026-03-01"},
            recomendacion="Revocar el acceso y notificar al responsable del area.",
        )
    )
    sesion.commit()
    hallazgo = sesion.query(m.Hallazgo).one()
    assert hallazgo.evidencia["dias_sin_uso"] == 215
    assert float(hallazgo.puntaje) == 42.5


def test_la_categoria_del_hallazgo_esta_acotada(sesion: Session) -> None:
    snapshot = m.Snapshot(etiqueta="D-1")
    sesion.add(snapshot)
    sesion.flush()
    sesion.add(
        m.Hallazgo(
            snapshot_id=snapshot.id,
            categoria="acceso_raro",
            severidad=Severidad.BAJA,
            evidencia={},
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_un_script_necesita_un_origen(sesion: Session) -> None:
    """Un script sin hallazgo ni solicitud no tiene con que compararse.

    La cadena de validacion incluye un control de correspondencia entre lo
    que el script hace y la decision del motor de reglas. Sin origen ese
    control no se puede ejecutar, asi que el registro no deberia existir.
    """
    sesion.add(
        m.ScriptGenerado(
            proveedor="gemini",
            modelo="gemini-flash-latest",
            lenguaje="powershell",
            contenido="Remove-ADGroupMember ...",
            controles={},
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_la_aprobacion_de_un_script_es_completa_o_no_existe(sesion: Session) -> None:
    """No puede haber un aprobador sin fecha ni una fecha sin aprobador.

    Es el registro que respalda la intervencion humana obligatoria del
    diseno, y a medias no sirve como evidencia de auditoria.
    """
    snapshot = m.Snapshot(etiqueta="D-1")
    sesion.add(snapshot)
    sesion.flush()
    hallazgo = m.Hallazgo(
        snapshot_id=snapshot.id,
        categoria=Categoria.CONFLICTO_SOD,
        severidad=Severidad.CRITICA,
        evidencia={"regla": "SOD-001"},
    )
    sesion.add(hallazgo)
    sesion.flush()
    sesion.add(
        m.ScriptGenerado(
            hallazgo_id=hallazgo.id,
            proveedor="gemini",
            modelo="gemini-flash-latest",
            lenguaje="powershell",
            contenido="Remove-ADGroupMember ...",
            estado_validacion="valido",
            controles={"esquema": True, "sintaxis": True},
            aprobado_por="U00001",
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()


def test_el_script_aprobado_guarda_quien_y_cuando(sesion: Session) -> None:
    snapshot = m.Snapshot(etiqueta="D-1")
    sesion.add(snapshot)
    sesion.flush()
    hallazgo = m.Hallazgo(
        snapshot_id=snapshot.id,
        categoria=Categoria.CONFLICTO_SOD,
        severidad=Severidad.CRITICA,
        evidencia={"regla": "SOD-001"},
    )
    sesion.add(hallazgo)
    sesion.flush()
    sesion.add(
        m.ScriptGenerado(
            hallazgo_id=hallazgo.id,
            proveedor="ollama",
            modelo="llama3",
            lenguaje="sql",
            contenido="DELETE FROM asignaciones WHERE ...",
            estado_validacion="valido",
            controles={"esquema": True, "lista_blanca": True},
            aprobado_por="U00007",
            aprobado_en=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        )
    )
    sesion.commit()
    script = sesion.query(m.ScriptGenerado).one()
    assert script.aprobado_por == "U00007"
    assert script.controles["lista_blanca"] is True


def test_el_evento_de_auditoria_se_fecha_solo(sesion: Session) -> None:
    sesion.add(
        m.EventoAuditoria(
            actor="U00001",
            accion="carga_dataset",
            entidad="snapshot",
            entidad_id=1,
            detalle={"archivo": "D-2/asignaciones.csv"},
        )
    )
    sesion.commit()
    evento = sesion.query(m.EventoAuditoria).one()
    assert evento.ocurrido_en is not None


def test_la_clave_ajena_se_valida(sesion: Session) -> None:
    sesion.add(
        m.Permiso(
            aplicacion_id=9_999,
            codigo="FANTASMA",
            descripcion="apunta a una aplicacion que no existe",
            nivel=NivelPrivilegio.LECTURA,
            peso_riesgo=1,
        )
    )
    with pytest.raises(IntegrityError):
        sesion.flush()
