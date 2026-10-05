"""Prueba del arnes de medicion.

El script que produce los numeros del plan de pruebas tambien tiene que
estar verificado: si midiera mal, los resultados del capitulo de validacion
serian incorrectos sin que nada lo delate. Se corre sobre el conjunto chico
y contra SQLite, que es suficiente para comprobar que arma bien las
metricas; los valores que se reportan en el documento se miden sobre
PostgreSQL.
"""
from __future__ import annotations

from herramientas.medir import medir_perfil


def test_el_arnes_reporta_metricas_coherentes(tmp_path) -> None:
    datos = medir_perfil("D-1", tmp_path, f"sqlite+pysqlite:///{tmp_path / 'm.db'}")

    assert datos["perfil"] == "D-1"
    assert datos["importacion"]["filas_aceptadas"] > 0
    assert datos["analisis"]["hallazgos"] > 0
    assert datos["analisis"]["segundos"] > 0

    consolidado = datos["i02"]["consolidado"]
    assert consolidado["verdaderos_positivos"] > 0
    assert consolidado["precision"] == 1.0
    assert consolidado["exhaustividad"] == 1.0

    for metricas in datos["i02"]["por_categoria"].values():
        assert metricas["precision"] + metricas["exhaustividad"] > 0
        assert (
            metricas["verdaderos_positivos"] + metricas["falsos_negativos"]
            == metricas["plantados"]
        )
