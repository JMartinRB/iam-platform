"""Pruebas del interprete de expresiones de atributos.

Este interprete decide si una politica se aplica, asi que un error aca se
traduce en un acceso otorgado o negado de mas. Las pruebas cubren las tres
cosas que el capitulo de marco tecnologico promete que soporta
—comparaciones, pertenencia a conjuntos y operadores logicos— y, sobre
todo, que no se pueda colar nada que se ejecute.
"""
from __future__ import annotations

import pytest

from app.modulos.expresiones import ErrorExpresion, describir, evaluar, validar

ATRIBUTOS = {
    "area": "Finanzas",
    "puesto": "Analista de pagos",
    "ubicacion": "Buenos Aires",
    "estado": "activo",
    "legajo": "U00042",
    "jefe_legajo": "U00001",
    "antiguedad_dias": 400,
}


# ---------------------------------------------------------------------------
# Comparaciones
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expresion", "esperado"),
    [
        ({"igual": ["area", "Finanzas"]}, True),
        ({"igual": ["area", "Compras"]}, False),
        ({"distinto": ["estado", "baja"]}, True),
        ({"en": ["area", ["Finanzas", "Compras"]]}, True),
        ({"en": ["area", ["Compras"]]}, False),
        ({"no_en": ["ubicacion", ["Remoto"]]}, True),
        ({"mayor": ["antiguedad_dias", 180]}, True),
        ({"mayor": ["antiguedad_dias", 500]}, False),
        ({"menor": ["antiguedad_dias", 30]}, False),
        ({"existe": "jefe_legajo"}, True),
    ],
)
def test_comparaciones(expresion: dict, esperado: bool) -> None:
    assert evaluar(expresion, ATRIBUTOS) is esperado


def test_la_comparacion_de_texto_ignora_mayusculas_y_espacios() -> None:
    """Los exports escriben la misma area de varias formas.

    Una politica que no se aplica por una diferencia de mayusculas es peor
    que no tenerla: da la impresion de estar protegiendo algo.
    """
    assert evaluar({"igual": ["area", "  finanzas "]}, ATRIBUTOS) is True
    assert evaluar({"en": ["area", ["FINANZAS"]]}, ATRIBUTOS) is True


def test_un_atributo_ausente_no_satisface_una_comparacion_numerica() -> None:
    assert evaluar({"mayor": ["antiguedad_dias", 10]}, {}) is False
    assert evaluar({"existe": "jefe_legajo"}, {"jefe_legajo": ""}) is False


# ---------------------------------------------------------------------------
# Operadores logicos
# ---------------------------------------------------------------------------


def test_operadores_logicos() -> None:
    y = {"y": [{"igual": ["area", "Finanzas"]}, {"igual": ["estado", "activo"]}]}
    o = {"o": [{"igual": ["area", "Compras"]}, {"igual": ["area", "Finanzas"]}]}
    no = {"no": {"igual": ["area", "Tecnologia"]}}
    assert evaluar(y, ATRIBUTOS) is True
    assert evaluar(o, ATRIBUTOS) is True
    assert evaluar(no, ATRIBUTOS) is True


def test_anidamiento_profundo() -> None:
    expresion = {
        "y": [
            {"no": {"en": ["ubicacion", ["Remoto"]]}},
            {
                "o": [
                    {"igual": ["area", "Finanzas"]},
                    {"y": [{"igual": ["area", "Compras"]}, {"mayor": ["antiguedad_dias", 90]}]},
                ]
            },
        ]
    }
    validar(expresion)
    assert evaluar(expresion, ATRIBUTOS) is True


# ---------------------------------------------------------------------------
# Lo que el interprete tiene que rechazar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expresion",
    [
        "area == 'Finanzas'",
        {"igual": ["area", "Finanzas"], "o": []},
        {"ejecutar": ["os.system", "rm -rf /"]},
        {"igual": ["sueldo", 100]},
        {"igual": ["area"]},
        {"en": ["area", "Finanzas"]},
        {"mayor": ["antiguedad_dias", "mucho"]},
        {"y": []},
        {},
    ],
)
def test_las_expresiones_invalidas_se_rechazan_al_validar(expresion: object) -> None:
    with pytest.raises(ErrorExpresion):
        validar(expresion)


def test_no_se_puede_referenciar_un_atributo_que_no_existe() -> None:
    """Un atributo desconocido es un error, no un valor nulo.

    Si evaluara como falso en silencio, una politica con un nombre mal
    escrito dejaria de aplicarse sin que nadie se entere, que es la peor
    forma de fallar para un control de accesos.
    """
    with pytest.raises(ErrorExpresion, match="atributo desconocido"):
        evaluar({"igual": ["salario", 100]}, ATRIBUTOS)


def test_la_recursion_esta_acotada() -> None:
    expresion: dict = {"igual": ["area", "Finanzas"]}
    for _ in range(15):
        expresion = {"no": expresion}
    with pytest.raises(ErrorExpresion, match="anidada"):
        validar(expresion)


# ---------------------------------------------------------------------------
# Presentacion
# ---------------------------------------------------------------------------


def test_la_expresion_se_describe_en_castellano() -> None:
    """La pantalla tiene que poder explicar por que se nego un acceso."""
    assert describir({"igual": ["area", "Finanzas"]}) == "area es Finanzas"
    assert describir({"no": {"igual": ["area", "Finanzas"]}}) == "no area es Finanzas"
    texto = describir(
        {"y": [{"igual": ["estado", "activo"]}, {"mayor": ["antiguedad_dias", 30]}]}
    )
    assert " y " in texto
    assert "antiguedad_dias es mayor que 30" in texto
