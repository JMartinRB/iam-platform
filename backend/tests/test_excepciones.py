"""Pruebas de las excepciones justificadas (RF-11).

La más importante del archivo es la última: que marcar una excepción no
altere lo que mide el indicador I-02. El motor tiene que seguir emitiendo
el hallazgo; lo que cambia es que el panel no lo muestre. Si alguna vez
alguien decide filtrar dentro de `analizar()` para simplificar, esa prueba
falla y explica por qué no se puede.
"""
from __future__ import annotations

import contextlib
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modulos import excepciones
from app.modulos.analisis import ParametrosAnalisis, analizar, resumen
from app.modulos.importacion import importar_dataset
from app.nucleo.modelos import EventoAuditoria, Hallazgo, Usuario

FECHA_CORTE = date(2026, 10, 1)


@pytest.fixture()
def analizado(sesion: Session, dataset_d1: dict) -> tuple[Session, list[Hallazgo]]:
    importacion = importar_dataset(sesion, dataset_d1["carpeta"])
    sesion.flush()
    hallazgos = analizar(
        sesion, importacion.snapshot_id, ParametrosAnalisis(fecha_corte=FECHA_CORTE)
    )
    sesion.flush()
    return sesion, hallazgos


def _con_regla(hallazgos: list[Hallazgo]) -> Hallazgo:
    """Un conflicto de SoD: la única categoría que identifica su regla."""
    return next(h for h in hallazgos if h.regla_sod_id and h.usuario_id)


def _sin_regla(hallazgos: list[Hallazgo]) -> Hallazgo:
    """Un hallazgo de cualquier otra categoría, sin regla asociada."""
    return next(h for h in hallazgos if not h.regla_sod_id and h.usuario_id)


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------


def test_una_excepcion_tapa_su_hallazgo(analizado) -> None:
    sesion, hallazgos = analizado
    objetivo = _con_regla(hallazgos)

    excepciones.registrar(
        sesion, objetivo, motivo="aprobado por el negocio", responsable="U00009"
    )

    anotados = excepciones.anotar(hallazgos, excepciones.vigentes(sesion))
    tapado = next(a for a in anotados if a.hallazgo.id == objetivo.id)
    assert tapado.exceptuado
    assert tapado.excepcion is not None
    assert tapado.excepcion.motivo == "aprobado por el negocio"


def test_la_excepcion_de_una_regla_no_tapa_las_demas(analizado) -> None:
    """Un usuario puede infringir dos reglas y aprobarse sólo una."""
    sesion, hallazgos = analizado
    objetivo = _con_regla(hallazgos)

    excepciones.registrar(
        sesion, objetivo, motivo="caso puntual", responsable="U00009"
    )

    anotados = excepciones.anotar(hallazgos, excepciones.vigentes(sesion))
    tapados = [a.hallazgo for a in anotados if a.exceptuado]
    assert all(h.regla_sod_id == objetivo.regla_sod_id for h in tapados)
    assert all(h.usuario_id == objetivo.usuario_id for h in tapados)


def test_en_una_categoria_sin_regla_el_alcance_es_la_categoria(analizado) -> None:
    """Cuatro de las cinco categorías no identifican regla, así que la
    excepción cubre la categoría entera para ese usuario."""
    sesion, hallazgos = analizado
    objetivo = _sin_regla(hallazgos)

    excepcion = excepciones.registrar(
        sesion, objetivo, motivo="cuenta de servicio", responsable="U00009"
    )

    assert excepcion.regla_sod_id is None
    anotados = excepciones.anotar(hallazgos, excepciones.vigentes(sesion))
    tapados = [a.hallazgo for a in anotados if a.exceptuado]
    assert all(
        h.usuario_id == objetivo.usuario_id and h.categoria == objetivo.categoria
        for h in tapados
    )


def test_el_alcance_de_categoria_tapa_todos_los_de_ese_usuario(analizado) -> None:
    sesion, hallazgos = analizado
    objetivo = _con_regla(hallazgos)
    hermanos = [
        h
        for h in hallazgos
        if h.usuario_id == objetivo.usuario_id and h.categoria == objetivo.categoria
    ]

    excepciones.registrar(
        sesion,
        objetivo,
        motivo="cuenta de servicio, siempre va a figurar así",
        responsable="U00009",
        solo_esta_regla=False,
    )

    anotados = excepciones.anotar(hallazgos, excepciones.vigentes(sesion))
    tapados = {a.hallazgo.id for a in anotados if a.exceptuado}
    assert {h.id for h in hermanos} <= tapados


def test_una_excepcion_sin_motivo_se_rechaza(analizado) -> None:
    """Una excepción sin justificación escrita es indistinguible de haber
    apagado el hallazgo porque molestaba."""
    sesion, hallazgos = analizado

    with pytest.raises(excepciones.ErrorExcepcion):
        excepciones.registrar(
            sesion, _con_regla(hallazgos), motivo="   ", responsable="U00009"
        )


def test_una_excepcion_sin_responsable_se_rechaza(analizado) -> None:
    sesion, hallazgos = analizado

    with pytest.raises(excepciones.ErrorExcepcion):
        excepciones.registrar(
            sesion, _con_regla(hallazgos), motivo="algún motivo", responsable=""
        )


def test_no_se_duplica_una_excepcion_activa(analizado) -> None:
    sesion, hallazgos = analizado
    objetivo = _con_regla(hallazgos)
    excepciones.registrar(sesion, objetivo, motivo="primera", responsable="U00009")

    with pytest.raises(excepciones.ErrorExcepcion):
        excepciones.registrar(sesion, objetivo, motivo="segunda", responsable="U00009")


def test_registrar_y_revocar_quedan_en_la_auditoria(analizado) -> None:
    sesion, hallazgos = analizado
    excepcion = excepciones.registrar(
        sesion, _con_regla(hallazgos), motivo="justificado", responsable="U00009"
    )
    excepciones.revocar(sesion, excepcion, actor="U00010")

    acciones = {
        e.accion
        for e in sesion.scalars(
            select(EventoAuditoria).where(
                EventoAuditoria.entidad == "excepcion_hallazgo"
            )
        ).all()
    }
    assert acciones == {"registrar_excepcion", "revocar_excepcion"}


# ---------------------------------------------------------------------------
# Revocación
# ---------------------------------------------------------------------------


def test_revocar_deja_rastro_en_lugar_de_borrar(analizado) -> None:
    sesion, hallazgos = analizado
    excepcion = excepciones.registrar(
        sesion, _con_regla(hallazgos), motivo="justificado", responsable="U00009"
    )

    excepciones.revocar(sesion, excepcion, actor="U00010")

    assert excepcion.activa is False
    assert excepcion.revocada_por == "U00010"
    assert excepcion.revocada_en is not None
    assert excepcion.motivo == "justificado"
    assert excepciones.vigentes(sesion) == []


def test_revocar_devuelve_el_hallazgo_al_panel(analizado) -> None:
    sesion, hallazgos = analizado
    objetivo = _con_regla(hallazgos)
    excepcion = excepciones.registrar(
        sesion, objetivo, motivo="justificado", responsable="U00009"
    )
    excepciones.revocar(sesion, excepcion, actor="U00010")

    anotados = excepciones.anotar(hallazgos, excepciones.vigentes(sesion))
    assert not any(a.exceptuado for a in anotados)


def test_una_excepcion_revocada_se_puede_volver_a_poner(analizado) -> None:
    sesion, hallazgos = analizado
    objetivo = _con_regla(hallazgos)
    primera = excepciones.registrar(
        sesion, objetivo, motivo="justificado", responsable="U00009"
    )
    excepciones.revocar(sesion, primera, actor="U00010")

    segunda = excepciones.registrar(
        sesion, objetivo, motivo="sigue justificado", responsable="U00011"
    )

    # Es la misma fila reactivada: una por alcance, con su historial.
    assert segunda.id == primera.id
    assert segunda.activa is True
    assert segunda.motivo == "sigue justificado"


# ---------------------------------------------------------------------------
# La separación que protege la medición
# ---------------------------------------------------------------------------


def test_la_excepcion_no_cambia_lo_que_detecta_el_motor(analizado, dataset_d1) -> None:
    """El motor no sabe nada de excepciones, y tiene que seguir sin saber.

    El indicador I-02 mide la detección contra una verdad de referencia. Si
    `analizar()` filtrara las excepciones, un hallazgo plantado y
    exceptuado aparecería como falso negativo y la exhaustividad bajaría
    por una decisión administrativa en lugar de por un error del algoritmo.
    """
    sesion, hallazgos = analizado
    objetivo = _con_regla(hallazgos)
    antes = {h.id for h in hallazgos}

    excepciones.registrar(
        sesion, objetivo, motivo="aprobado", responsable="U00009", solo_esta_regla=False
    )
    sesion.commit()

    snapshot_id = hallazgos[0].snapshot_id
    for h in sesion.scalars(
        select(Hallazgo).where(Hallazgo.snapshot_id == snapshot_id)
    ).all():
        sesion.delete(h)
    sesion.flush()

    de_nuevo = analizar(sesion, snapshot_id, ParametrosAnalisis(fecha_corte=FECHA_CORTE))
    sesion.flush()

    # Los identificadores cambian al reinsertar; lo que tiene que coincidir
    # es el conjunto de casos detectados.
    clave = lambda h: (h.usuario_id, str(h.categoria), h.permiso_id)  # noqa: E731
    assert len(de_nuevo) == len(antes)
    assert {clave(h) for h in de_nuevo} == {clave(h) for h in hallazgos}


def test_el_indice_baja_cuando_se_exceptua(analizado) -> None:
    """Para el analista sí tiene que cambiar: un hallazgo justificado no es
    riesgo pendiente."""
    sesion, hallazgos = analizado
    usuarios = sesion.scalar(select(Usuario).with_only_columns(Usuario.id).limit(1))
    total_usuarios = len(sesion.scalars(select(Usuario)).all())
    assert usuarios is not None

    indice_inicial = resumen(hallazgos, total_usuarios)["indice_riesgo"]

    # Se exceptúa a todos los usuarios señalados de una categoría entera.
    criticos = [h for h in hallazgos if str(h.severidad) == "critica" and h.usuario_id]
    for h in criticos[:20]:
        # Puede estar ya exceptuado por otro hallazgo del mismo usuario.
        with contextlib.suppress(excepciones.ErrorExcepcion):
            excepciones.registrar(
                sesion,
                h,
                motivo="revisado y aceptado",
                responsable="U00009",
                solo_esta_regla=False,
            )

    anotados = excepciones.anotar(hallazgos, excepciones.vigentes(sesion))
    visibles = [a.hallazgo for a in anotados if not a.exceptuado]
    indice_final = resumen(visibles, total_usuarios)["indice_riesgo"]

    assert indice_final < indice_inicial
