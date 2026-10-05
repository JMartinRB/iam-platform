"""Normalizacion de codigos: el ruido de nomenclatura del dataset.

Cada caso de esta tabla corresponde a una variante que el generador puede
escribir en el CSV. Si una deja de resolver al codigo canonico, el
importador perderia asignaciones y la medicion de exhaustividad caeria por
un problema de parseo.
"""
from __future__ import annotations

import pytest

from app.nucleo.texto import (
    normalizar_codigo,
    normalizar_identificador,
    normalizar_texto,
)


@pytest.mark.parametrize(
    "entrada",
    [
        "ERP_CARGA_PAGOS",
        "erp_carga_pagos",
        "ERP-CARGA-PAGOS",
        "ERP.CARGA.PAGOS",
        "  ERP_CARGA_PAGOS  ",
        "Erp_Carga_Pagos",
        "ERP CARGA PAGOS",
        "ERP__CARGA___PAGOS",
        "erp/carga/pagos",
    ],
)
def test_las_variantes_resuelven_al_codigo_canonico(entrada: str) -> None:
    assert normalizar_codigo(entrada) == "ERP_CARGA_PAGOS"


def test_quita_tildes() -> None:
    assert normalizar_codigo("gestión-documental") == "GESTION_DOCUMENTAL"


def test_el_vacio_no_rompe() -> None:
    assert normalizar_codigo(None) == ""
    assert normalizar_codigo("   ") == ""


def test_no_une_codigos_distintos() -> None:
    """La normalizacion tiene que ser laxa, no indiscriminada."""
    assert normalizar_codigo("ERP_CONSULTA") != normalizar_codigo("ERP_CONSULTAS")
    assert normalizar_codigo("TES_CARGA_PAGOS") != normalizar_codigo("ERP_CARGA_PAGOS")


def test_normalizar_texto_colapsa_espacios() -> None:
    assert normalizar_texto("  Juan   Martin  ") == "Juan Martin"
    assert normalizar_texto("Gestión") == "Gestión", "no debe tocar los acentos"


def test_el_identificador_conserva_sus_separadores() -> None:
    """Un codigo de regla es una etiqueta, no una clave de cruce."""
    assert normalizar_identificador("  sod-009 ") == "SOD-009"
    assert normalizar_identificador("abac 001") == "ABAC_001"
    assert normalizar_identificador(None) == ""
