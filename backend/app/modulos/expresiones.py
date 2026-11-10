"""Interprete acotado de expresiones sobre atributos.

Las politicas ABAC se guardan como una expresion en JSON y se evaluan con
este interprete, que soporta comparaciones, pertenencia a conjuntos y
operadores logicos. Es deliberadamente limitado: no hay aritmetica, no hay
llamadas a funciones y no hay forma de referirse a nada que no sea un
atributo del usuario que se esta evaluando.

La alternativa rapida habria sido guardar la condicion como una cadena de
Python y resolverla con eval. Ademas de ser la via clasica de ejecucion de
codigo arbitrario, en un sistema cuyo tema es el control de accesos seria
una contradiccion difícil de defender: la politica que decide quien accede
a que no puede ser, ella misma, un vector de ejecucion.

Una expresion es un diccionario con una sola clave, que es el operador:

    {"igual": ["area", "Finanzas"]}
    {"en": ["ubicacion", ["Buenos Aires", "Cordoba"]]}
    {"y": [{"igual": ["estado", "activo"]}, {"mayor": ["antiguedad_dias", 180]}]}
    {"no": {"igual": ["area", "Tecnologia"]}}

El primer operando de una comparacion es siempre el nombre de un atributo;
el segundo es un literal. Esa asimetria es intencional: evita tener que
decidir si una cadena es un valor o una referencia.
"""
from __future__ import annotations

from typing import Any

#: Atributos que una politica puede consultar. Cualquier otro nombre es un
#: error de definicion, no un valor nulo: es preferible que la politica
#: falle a que silenciosamente evalue como falsa y deje pasar un acceso.
ATRIBUTOS = frozenset(
    {
        "area",
        "puesto",
        "ubicacion",
        "estado",
        "legajo",
        "jefe_legajo",
        "antiguedad_dias",
    }
)

OPERADORES_LOGICOS = frozenset({"y", "o", "no"})
OPERADORES_COMPARACION = frozenset(
    {"igual", "distinto", "en", "no_en", "mayor", "menor", "existe"}
)


class ErrorExpresion(ValueError):
    """La expresion esta mal formada o usa un atributo desconocido."""


def validar(expresion: Any, _profundidad: int = 0) -> None:
    """Comprueba que la expresion sea evaluable antes de guardarla.

    Se valida al cargar la politica y no al evaluarla, para que un error de
    definicion se descubra en la importacion y no en medio de una decision
    de acceso.
    """
    if _profundidad > 10:
        raise ErrorExpresion("la expresion esta anidada demasiado profundo")
    if not isinstance(expresion, dict) or len(expresion) != 1:
        raise ErrorExpresion(
            "cada nodo debe ser un diccionario con exactamente un operador"
        )

    operador, operandos = next(iter(expresion.items()))

    if operador in ("y", "o"):
        if not isinstance(operandos, list) or not operandos:
            raise ErrorExpresion(f"'{operador}' espera una lista no vacia")
        for sub in operandos:
            validar(sub, _profundidad + 1)
        return

    if operador == "no":
        validar(operandos, _profundidad + 1)
        return

    if operador not in OPERADORES_COMPARACION:
        raise ErrorExpresion(f"operador desconocido: {operador}")

    if operador == "existe":
        atributo, valor = operandos, None
    else:
        if not isinstance(operandos, list) or len(operandos) != 2:
            raise ErrorExpresion(f"'{operador}' espera [atributo, valor]")
        atributo, valor = operandos

    if not isinstance(atributo, str) or atributo not in ATRIBUTOS:
        raise ErrorExpresion(f"atributo desconocido: {atributo!r}")

    if operador in ("en", "no_en") and not isinstance(valor, list):
        raise ErrorExpresion(f"'{operador}' espera una lista de valores")
    if operador in ("mayor", "menor") and not isinstance(valor, int | float):
        raise ErrorExpresion(f"'{operador}' espera un numero")


def evaluar(expresion: Any, atributos: dict[str, Any]) -> bool:
    """Evalua la expresion contra los atributos de un usuario."""
    if not isinstance(expresion, dict) or len(expresion) != 1:
        raise ErrorExpresion("expresion mal formada")

    operador, operandos = next(iter(expresion.items()))

    if operador == "y":
        return all(evaluar(sub, atributos) for sub in operandos)
    if operador == "o":
        return any(evaluar(sub, atributos) for sub in operandos)
    if operador == "no":
        return not evaluar(operandos, atributos)

    if operador == "existe":
        return atributos.get(operandos) not in (None, "")

    atributo, esperado = operandos
    if atributo not in ATRIBUTOS:
        raise ErrorExpresion(f"atributo desconocido: {atributo!r}")
    actual = atributos.get(atributo)

    if operador == "igual":
        return _comparable(actual) == _comparable(esperado)
    if operador == "distinto":
        return _comparable(actual) != _comparable(esperado)
    if operador == "en":
        return _comparable(actual) in {_comparable(v) for v in esperado}
    if operador == "no_en":
        return _comparable(actual) not in {_comparable(v) for v in esperado}

    # Las comparaciones numericas no aplican sobre un atributo ausente.
    if actual is None:
        return False
    try:
        if operador == "mayor":
            return float(actual) > float(esperado)
        if operador == "menor":
            return float(actual) < float(esperado)
    except (TypeError, ValueError):
        return False

    raise ErrorExpresion(f"operador desconocido: {operador}")


def _comparable(valor: Any) -> Any:
    """Las cadenas se comparan sin distinguir mayusculas ni espacios.

    Los datos vienen de exports de distintas plataformas y la misma area
    aparece escrita de varias formas. Una politica que no se aplica por una
    diferencia de mayusculas es peor que inutil: da la impresion de estar
    protegiendo algo.
    """
    if isinstance(valor, str):
        return valor.strip().casefold()
    return valor


def describir(expresion: Any) -> str:
    """Traduce la expresion a una frase legible, para mostrar en pantalla."""
    operador, operandos = next(iter(expresion.items()))

    if operador in ("y", "o"):
        union = " y " if operador == "y" else " o "
        return "(" + union.join(describir(sub) for sub in operandos) + ")"
    if operador == "no":
        return f"no {describir(operandos)}"
    if operador == "existe":
        return f"tiene {operandos}"

    atributo, valor = operandos
    frases = {
        "igual": f"{atributo} es {valor}",
        "distinto": f"{atributo} no es {valor}",
        "en": f"{atributo} esta entre {', '.join(map(str, valor))}"
        if isinstance(valor, list)
        else "",
        "no_en": f"{atributo} no esta entre {', '.join(map(str, valor))}"
        if isinstance(valor, list)
        else "",
        "mayor": f"{atributo} es mayor que {valor}",
        "menor": f"{atributo} es menor que {valor}",
    }
    return frases.get(operador, operador)
