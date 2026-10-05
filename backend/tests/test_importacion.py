"""Pruebas del importador.

El caso de aceptacion CP-01 pide que una carga con filas malformadas importe
las validas y liste las rechazadas con su motivo. Eso es lo que se verifica
aca, usando el dataset sintetico, que trae el ruido declarado en su
manifiesto: asi la prueba sabe de antemano cuantas filas tienen que entrar,
cuantas tienen que caer y por que.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modulos.importacion import (
    ErrorImportacion,
    importar_dataset,
    resumen,
)
from app.nucleo.modelos import (
    Aplicacion,
    AsignacionAcceso,
    EventoAuditoria,
    Permiso,
    ReglaSoD,
    Snapshot,
    Usuario,
)
from tests.conftest import FECHA_CORTE


@pytest.fixture()
def importado(sesion: Session, dataset_d1: dict) -> dict:
    resultado = importar_dataset(sesion, dataset_d1["carpeta"], actor="U00001")
    sesion.commit()
    return {"resultado": resultado, "manifiesto": dataset_d1["manifiesto"]}


def test_entran_todas_las_asignaciones_validas(importado: dict, sesion: Session) -> None:
    volumen = importado["manifiesto"]["volumen"]
    assert sesion.scalar(select(func.count()).select_from(AsignacionAcceso)) == (
        volumen["asignaciones"]
    )
    assert sesion.scalar(select(func.count()).select_from(Permiso)) == volumen["permisos"]
    assert sesion.scalar(select(func.count()).select_from(ReglaSoD)) == (
        volumen["reglas_sod"]
    )
    assert sesion.scalar(select(func.count()).select_from(Aplicacion)) == (
        volumen["aplicaciones"]
    )


def test_los_usuarios_duplicados_entran_una_sola_vez(
    importado: dict, sesion: Session
) -> None:
    """El export trae la misma persona repetida; la base guarda una."""
    manifiesto = importado["manifiesto"]
    unicos = manifiesto["volumen"]["usuarios"] - manifiesto["ruido"]["usuarios_duplicados"]
    assert sesion.scalar(select(func.count()).select_from(Usuario)) == unicos


def test_las_filas_malformadas_se_rechazan_con_su_motivo(importado: dict) -> None:
    resultado = importado["resultado"]
    manifiesto = importado["manifiesto"]

    esperados = (
        manifiesto["ruido"]["filas_malformadas"]
        + manifiesto["ruido"]["usuarios_duplicados"]
    )
    assert resultado.descartadas == esperados

    motivos = resultado.motivos()
    assert motivos["legajo vacio"] == 1
    assert motivos["usuario inexistente"] == 1
    assert motivos["permiso inexistente"] == 1
    assert motivos["origen fuera del dominio"] == 1
    assert motivos["fecha de otorgamiento invalida"] == 1
    assert motivos["legajo duplicado"] == manifiesto["ruido"]["usuarios_duplicados"]


def test_cada_rechazo_dice_en_que_linea_estaba(importado: dict) -> None:
    """Sin el numero de linea el analista no puede ir a corregir el origen."""
    for rechazo in importado["resultado"].rechazos:
        assert rechazo.linea >= 2, "la linea 1 es el encabezado"
        assert rechazo.archivo.endswith(".csv")
        assert rechazo.motivo
        assert str(rechazo).startswith(rechazo.archivo)


def test_los_codigos_con_otra_nomenclatura_se_resuelven(
    importado: dict, sesion: Session
) -> None:
    """Las variantes de escritura no deben perder asignaciones.

    El dataset escribe parte de los codigos con guiones, puntos, minusculas
    o espacios. Si el importador no normalizara, esas filas se rechazarian
    como permiso inexistente y el analisis mediria sobre menos datos de los
    que hay.
    """
    alternativos = importado["manifiesto"]["ruido"][
        "codigos_con_nomenclatura_alternativa"
    ]
    assert alternativos > 0
    assert "permiso inexistente" in importado["resultado"].motivos()
    # solo la fila deliberadamente invalida, ninguna de las deformadas
    assert importado["resultado"].motivos()["permiso inexistente"] == 1


def test_el_snapshot_guarda_el_recuento_y_la_huella(
    importado: dict, sesion: Session
) -> None:
    snapshot = sesion.scalars(select(Snapshot)).one()
    resultado = importado["resultado"]
    assert snapshot.filas_leidas == resultado.leidas
    assert snapshot.filas_descartadas == resultado.descartadas
    assert snapshot.hash_archivo and len(snapshot.hash_archivo) == 64


def test_la_carga_queda_auditada(importado: dict, sesion: Session) -> None:
    evento = sesion.scalars(
        select(EventoAuditoria).where(EventoAuditoria.accion == "importar_dataset")
    ).one()
    assert evento.actor == "U00001"
    assert evento.detalle["aceptadas"] == importado["resultado"].aceptadas
    assert evento.detalle["motivos"]


def test_las_asignaciones_por_rol_conservan_el_rol(
    importado: dict, sesion: Session
) -> None:
    sin_rol = sesion.scalar(
        select(func.count())
        .select_from(AsignacionAcceso)
        .where(AsignacionAcceso.origen == "rbac_rol", AsignacionAcceso.rol_id.is_(None))
    )
    assert sin_rol == 0


def test_las_fechas_se_leen_como_fechas(importado: dict, sesion: Session) -> None:
    usuario = sesion.scalars(select(Usuario).limit(1)).one()
    assert isinstance(usuario.fecha_ingreso, date)
    assert usuario.fecha_ingreso < FECHA_CORTE


def test_falta_un_archivo(sesion: Session, tmp_path) -> None:
    (tmp_path / "usuarios.csv").write_text("legajo\n", encoding="utf-8")
    with pytest.raises(ErrorImportacion, match="faltan archivos"):
        importar_dataset(sesion, tmp_path)


def test_el_resumen_es_serializable(importado: dict) -> None:
    datos = resumen(importado["resultado"])
    assert datos["filas_aceptadas"] > 0
    assert isinstance(datos["motivos"], dict)
    assert all(isinstance(x, str) for x in datos["rechazos"])
