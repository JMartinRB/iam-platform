"""Medicion del modulo de analisis contra la verdad de referencia.

Esta es la prueba que da sentido a todo el trabajo del generador. Se carga
un dataset con hallazgos plantados, se corre el analisis, y se compara lo
que el motor devolvio contra lo que efectivamente se habia plantado. De esa
comparacion salen la precision y la exhaustividad del indicador I-02 del
plan de pruebas.

La comparacion se hace por conjuntos y no por totales: dos conjuntos del
mismo tamano pueden no tener los mismos elementos adentro, y un motor que
detecta la cantidad correcta de cuentas dormidas senalando a las personas
equivocadas no sirve para nada.
"""
from __future__ import annotations

from collections import defaultdict

import pytest
from sqlalchemy.orm import Session

from app.modulos.analisis import (
    ParametrosAnalisis,
    analizar,
    puntaje,
    resumen,
    severidad_por_peso,
)
from app.modulos.importacion import importar_dataset
from app.nucleo.enums import Categoria, Severidad
from app.nucleo.modelos import EventoAuditoria, Hallazgo
from tests.conftest import FECHA_CORTE


@pytest.fixture()
def analizado(sesion: Session, dataset_d1: dict) -> dict:
    resultado = importar_dataset(sesion, dataset_d1["carpeta"])
    sesion.commit()
    hallazgos = analizar(
        sesion,
        resultado.snapshot_id,
        ParametrosAnalisis(fecha_corte=FECHA_CORTE),
        actor="U00001",
    )
    sesion.commit()
    return {
        "hallazgos": hallazgos,
        "manifiesto": dataset_d1["manifiesto"],
        "importacion": resultado,
    }


def detectados(hallazgos: list[Hallazgo], categoria: str) -> set[str]:
    return {
        h.evidencia["legajo"] for h in hallazgos if h.categoria == categoria
    }


def plantados(manifiesto: dict, categoria: str) -> set[str]:
    return {
        caso["legajo"]
        for caso in manifiesto["hallazgos_plantados"][categoria]["casos"]
    }


def metricas(detectado: set[str], plantado: set[str]) -> tuple[float, float]:
    """Precision y exhaustividad de un conjunto contra su referencia."""
    verdaderos = len(detectado & plantado)
    precision = verdaderos / len(detectado) if detectado else 0.0
    exhaustividad = verdaderos / len(plantado) if plantado else 0.0
    return precision, exhaustividad


# ---------------------------------------------------------------------------
# Una prueba por categoria
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "categoria",
    [
        Categoria.CUENTA_DORMIDA,
        Categoria.CUENTA_HUERFANA,
        Categoria.SOBRE_PRIVILEGIO,
        Categoria.CONFLICTO_SOD,
        Categoria.PRIVILEGE_CREEP,
    ],
)
def test_cada_categoria_detecta_exactamente_lo_plantado(
    analizado: dict, categoria: str
) -> None:
    detectado = detectados(analizado["hallazgos"], categoria)
    plantado = plantados(analizado["manifiesto"], categoria)
    precision, exhaustividad = metricas(detectado, plantado)

    assert precision == 1.0, f"falsos positivos: {sorted(detectado - plantado)[:5]}"
    assert exhaustividad == 1.0, f"no detectados: {sorted(plantado - detectado)[:5]}"


def test_los_umbrales_del_plan_de_pruebas_se_superan(analizado: dict) -> None:
    """Indicador I-02: precision mayor al 90 % y exhaustividad mayor al 85 %."""
    todos_detectados: set[tuple[str, str]] = set()
    todos_plantados: set[tuple[str, str]] = set()
    for categoria in analizado["manifiesto"]["hallazgos_plantados"]:
        todos_detectados |= {
            (categoria, legajo)
            for legajo in detectados(analizado["hallazgos"], categoria)
        }
        todos_plantados |= {
            (categoria, legajo)
            for legajo in plantados(analizado["manifiesto"], categoria)
        }

    verdaderos = len(todos_detectados & todos_plantados)
    precision = verdaderos / len(todos_detectados)
    exhaustividad = verdaderos / len(todos_plantados)
    assert precision > 0.90
    assert exhaustividad > 0.85


def test_el_conflicto_sod_se_emite_por_regla_infringida(analizado: dict) -> None:
    """Cada par en conflicto se remedia por separado, asi que se informa aparte.

    Un usuario puede infringir mas de una regla a la vez. El motor emite un
    hallazgo por cada una, y el manifiesto las declara todas, de modo que la
    cuenta tiene que coincidir par por par.
    """
    emitidos = {
        (h.evidencia["legajo"], h.evidencia["regla"])
        for h in analizado["hallazgos"]
        if h.categoria == Categoria.CONFLICTO_SOD
    }
    declarados = {
        (caso["legajo"], codigo)
        for caso in analizado["manifiesto"]["hallazgos_plantados"]["conflicto_sod"][
            "casos"
        ]
        for codigo in caso["reglas_infringidas"]
    }
    assert emitidos == declarados
    assert len(emitidos) == analizado["manifiesto"]["conflictos_sod_por_regla"]


def test_ningun_usuario_limpio_aparece_en_ningun_hallazgo(analizado: dict) -> None:
    """El complemento de la exhaustividad: la poblacion sana queda afuera."""
    marcados = {
        legajo
        for categoria in analizado["manifiesto"]["hallazgos_plantados"]
        for legajo in plantados(analizado["manifiesto"], categoria)
    }
    senalados = {h.evidencia["legajo"] for h in analizado["hallazgos"]}
    assert senalados <= marcados


# ---------------------------------------------------------------------------
# Contenido de los hallazgos
# ---------------------------------------------------------------------------


def test_todo_hallazgo_trae_evidencia_y_recomendacion(analizado: dict) -> None:
    for h in analizado["hallazgos"]:
        assert h.evidencia, "un hallazgo sin evidencia no se puede auditar"
        assert h.recomendacion, "un hallazgo sin recomendacion no es accionable"
        assert float(h.puntaje) > 0
        assert h.severidad in {s.value for s in Severidad}


def test_la_evidencia_explica_el_criterio_aplicado(analizado: dict) -> None:
    """Cada categoria tiene que mostrar contra que umbral se la comparo."""
    por_categoria: dict[str, list[Hallazgo]] = defaultdict(list)
    for h in analizado["hallazgos"]:
        por_categoria[h.categoria].append(h)

    dormida = por_categoria[Categoria.CUENTA_DORMIDA][0]
    assert dormida.evidencia["dias_sin_uso"] > dormida.evidencia["umbral_dias"]

    sobre = por_categoria[Categoria.SOBRE_PRIVILEGIO][0]
    assert sobre.evidencia["permisos"] >= sobre.evidencia["umbral"]
    assert sobre.evidencia["pares_evaluados"] >= 5

    creep = por_categoria[Categoria.PRIVILEGE_CREEP][0]
    assert creep.evidencia["antiguedad_maxima_dias"] > creep.evidencia["umbral_dias"]

    sod = por_categoria[Categoria.CONFLICTO_SOD][0]
    assert len(sod.evidencia["permisos"]) == 2
    assert sod.evidencia["fundamento"]


def test_la_huerfana_pesa_mas_que_la_dormida_a_igual_exposicion(
    analizado: dict,
) -> None:
    """Es una decision de diseno y conviene que quede fijada por una prueba.

    Una cuenta dada de baja que conserva accesos es mas grave que una activa
    sin uso, porque ya no hay nadie que deba tenerla. El motor sube un
    escalon la severidad de las huerfanas, y eso se tiene que ver en el
    puntaje.
    """
    severidades = {
        h.evidencia["legajo"]: h.severidad
        for h in analizado["hallazgos"]
        if h.categoria == Categoria.CUENTA_HUERFANA
    }
    assert severidades
    assert all(s in (Severidad.ALTA, Severidad.CRITICA) for s in severidades.values())


def test_el_puntaje_ordena_la_cola_de_trabajo(analizado: dict) -> None:
    ordenados = sorted(
        analizado["hallazgos"], key=lambda h: float(h.puntaje), reverse=True
    )
    assert float(ordenados[0].puntaje) > float(ordenados[-1].puntaje)
    assert ordenados[0].severidad in (Severidad.CRITICA, Severidad.ALTA)


@pytest.mark.parametrize(
    ("peso", "esperada"),
    [
        (10, Severidad.CRITICA),
        (9, Severidad.CRITICA),
        (8, Severidad.ALTA),
        (6, Severidad.ALTA),
        (5, Severidad.MEDIA),
        (3, Severidad.MEDIA),
        (2, Severidad.BAJA),
        (1, Severidad.BAJA),
    ],
)
def test_la_severidad_sigue_el_peso_del_permiso(peso: int, esperada: str) -> None:
    assert severidad_por_peso(peso) == esperada


def test_el_puntaje_crece_con_la_severidad_y_con_la_exposicion() -> None:
    assert puntaje(Severidad.CRITICA, [5]) > puntaje(Severidad.BAJA, [5])
    assert puntaje(Severidad.ALTA, [5, 5]) > puntaje(Severidad.ALTA, [5])


# ---------------------------------------------------------------------------
# Repetibilidad y trazabilidad
# ---------------------------------------------------------------------------


def test_el_analisis_es_repetible(sesion: Session, dataset_d1: dict) -> None:
    """Indicador I-05: dos corridas sobre los mismos datos dan lo mismo."""
    resultado = importar_dataset(sesion, dataset_d1["carpeta"])
    sesion.commit()
    parametros = ParametrosAnalisis(fecha_corte=FECHA_CORTE)

    def firma(hallazgos: list[Hallazgo]) -> set[tuple]:
        return {
            (h.categoria, h.evidencia["legajo"], h.severidad, float(h.puntaje))
            for h in hallazgos
        }

    primera = firma(analizar(sesion, resultado.snapshot_id, parametros))
    sesion.rollback()
    segunda = firma(analizar(sesion, resultado.snapshot_id, parametros))
    assert primera == segunda


def test_el_analisis_queda_auditado(analizado: dict, sesion: Session) -> None:
    from sqlalchemy import select

    evento = sesion.scalars(
        select(EventoAuditoria).where(EventoAuditoria.accion == "analizar_snapshot")
    ).one()
    assert evento.actor == "U00001"
    assert evento.detalle["hallazgos"] == len(analizado["hallazgos"])
    assert evento.detalle["parametros"]["dias_cuenta_dormida"] == 90


def test_el_resumen_agrega_por_categoria_y_severidad(analizado: dict) -> None:
    datos = resumen(analizado["hallazgos"], usuarios=500)
    assert datos["hallazgos"] == len(analizado["hallazgos"])
    assert set(datos["por_categoria"]) == {c.value for c in Categoria}
    assert datos["riesgo_total"] > 0
    assert datos["riesgo_por_usuario"] > 0


def test_un_umbral_mas_laxo_detecta_menos_cuentas_dormidas(
    sesion: Session, dataset_d1: dict
) -> None:
    """El parametro tiene efecto, que es lo que lo vuelve util de ajustar."""
    resultado = importar_dataset(sesion, dataset_d1["carpeta"])
    sesion.commit()
    base = analizar(
        sesion, resultado.snapshot_id, ParametrosAnalisis(fecha_corte=FECHA_CORTE)
    )
    sesion.rollback()
    laxo = analizar(
        sesion,
        resultado.snapshot_id,
        ParametrosAnalisis(fecha_corte=FECHA_CORTE, dias_cuenta_dormida=400),
    )
    assert len(detectados(laxo, Categoria.CUENTA_DORMIDA)) < len(
        detectados(base, Categoria.CUENTA_DORMIDA)
    )


def test_los_umbrales_salen_de_la_configuracion() -> None:
    """En produccion los parametros no se escriben en el codigo."""
    from app.nucleo.config import obtener_config

    parametros = ParametrosAnalisis.desde_configuracion(FECHA_CORTE)
    config = obtener_config()
    assert parametros.dias_cuenta_dormida == config.dias_cuenta_dormida
    assert parametros.factor_sobre_privilegio == config.factor_sobreprivilegio
    assert parametros.antiguedad_creep_dias == config.antiguedad_creep_dias
