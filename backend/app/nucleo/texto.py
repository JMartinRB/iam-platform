"""Normalizacion de los valores de texto que llegan en los exports.

El mismo permiso suele aparecer escrito de maneras distintas segun la
plataforma que generó el archivo: con guiones, con puntos, en minuscula o
con espacios al costado. El importador normaliza antes de buscar, de modo
que esas variantes resuelvan al mismo codigo.
"""
from __future__ import annotations

import re
import unicodedata

_SEPARADORES = re.compile(r"[\s\-.:/\\]+")
_REPETIDOS = re.compile(r"_{2,}")


def quitar_tildes(valor: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", valor) if not unicodedata.combining(c)
    )


def normalizar_codigo(valor: str | None) -> str:
    """Lleva un codigo a su forma canonica: mayusculas y guion bajo.

    >>> normalizar_codigo(" erp-carga.pagos ")
    'ERP_CARGA_PAGOS'
    """
    if valor is None:
        return ""
    texto = quitar_tildes(valor).strip()
    texto = _SEPARADORES.sub("_", texto)
    texto = _REPETIDOS.sub("_", texto)
    return texto.strip("_").upper()


def normalizar_identificador(valor: str | None) -> str:
    """Normaliza un identificador propio del archivo, sin unificar separadores.

    Se usa para codigos que son etiquetas y no claves de cruce, como el de
    una regla de Segregacion de Funciones. Ahi el guion es parte del nombre
    y convertirlo en guion bajo solo logra que el codigo que ve el analista
    no coincida con el que figura en su documentacion.

    >>> normalizar_identificador("  sod-009 ")
    'SOD-009'
    """
    if valor is None:
        return ""
    return re.sub(r"\s+", "_", quitar_tildes(valor).strip()).upper()


def normalizar_texto(valor: str | None) -> str:
    """Colapsa espacios y recorta, sin tocar mayusculas ni acentos."""
    if valor is None:
        return ""
    return re.sub(r"\s+", " ", valor).strip()
