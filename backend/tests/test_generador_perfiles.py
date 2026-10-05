"""Las mismas garantias, sobre los tres perfiles.

Las pruebas de ``test_generador`` trabajan sobre D-1 porque es rapido. Lo
que se verifica aca es que las invariantes que sostienen la medicion se
mantengan tambien en los volumenes grandes, que son los que se usan para
medir de verdad: D-2 para los indicadores y D-3 para el rendimiento. Un
generador que solo es correcto en el conjunto chico no sirve.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median

import pytest

from herramientas.generar_dataset import DIAS_VENTANA_USO, PERFILES, generar
from tests.conftest import FECHA_CORTE, leer_csv
from tests.test_generador import _activas_por_legajo, _fecha, _plantados, _validas

TABLAS = (
    "aplicaciones",
    "permisos",
    "roles",
    "roles_permisos",
    "usuarios",
    "asignaciones",
    "reglas_sod",
)


@pytest.fixture(scope="module", params=["D-2", "D-3"])
def dataset(request, tmp_path_factory) -> dict:
    perfil_id = request.param
    destino = tmp_path_factory.mktemp(f"ds-{perfil_id}")
    manifiesto = generar(perfil_id, destino, fecha_corte=FECHA_CORTE)
    carpeta = destino / perfil_id
    return {
        "perfil": perfil_id,
        "manifiesto": manifiesto,
        "tablas": {n: leer_csv(carpeta / f"{n}.csv") for n in TABLAS},
    }


def test_volumen_exacto(dataset: dict) -> None:
    perfil = PERFILES[dataset["perfil"]]
    assert dataset["manifiesto"]["volumen"]["asignaciones"] == perfil.asignaciones


def test_dormidas_exactas(dataset: dict) -> None:
    detectadas = {
        u["legajo"]
        for u in dataset["tablas"]["usuarios"]
        if u["estado"] == "activo"
        and _fecha(u["ultimo_acceso"]) is not None
        and (FECHA_CORTE - _fecha(u["ultimo_acceso"])).days > DIAS_VENTANA_USO
    }
    assert detectadas == _plantados(dataset["manifiesto"], "cuenta_dormida")


def test_huerfanas_exactas(dataset: dict) -> None:
    activas = _activas_por_legajo(dataset["tablas"])
    detectadas = {
        u["legajo"]
        for u in dataset["tablas"]["usuarios"]
        if u["estado"] == "baja" and activas.get(u["legajo"])
    }
    assert detectadas == _plantados(dataset["manifiesto"], "cuenta_huerfana")


def test_conflictos_sod_exactos(dataset: dict) -> None:
    reglas = [
        (r["permiso_a"], r["permiso_b"])
        for r in dataset["tablas"]["reglas_sod"]
        if r["activa"].lower() == "true"
    ]
    activas = _activas_por_legajo(dataset["tablas"])
    detectados = {
        legajo
        for legajo, permisos in activas.items()
        for a, b in reglas
        if a in permisos and b in permisos
    }
    assert detectados == _plantados(dataset["manifiesto"], "conflicto_sod")


def test_sobre_privilegio_exacto(dataset: dict) -> None:
    activas = _activas_por_legajo(dataset["tablas"])
    puesto_de = {u["legajo"]: u["puesto"] for u in dataset["tablas"]["usuarios"]}
    por_puesto: dict[str, list[int]] = defaultdict(list)
    for legajo, permisos in activas.items():
        por_puesto[puesto_de[legajo]].append(len(permisos))
    umbral = {p: 2 * median(v) for p, v in por_puesto.items()}
    detectados = {
        legajo
        for legajo, permisos in activas.items()
        if len(permisos) >= umbral[puesto_de[legajo]]
    }
    assert detectados == _plantados(dataset["manifiesto"], "sobre_privilegio")


def test_filas_malformadas_exactas(dataset: dict) -> None:
    tablas = dataset["tablas"]
    rechazadas = len(tablas["asignaciones"]) - len(_validas(tablas))
    assert rechazadas == dataset["manifiesto"]["ruido"]["filas_malformadas"]


def test_ningun_rol_en_conflicto(dataset: dict) -> None:
    permisos_de_rol: dict[str, set[str]] = defaultdict(set)
    for fila in dataset["tablas"]["roles_permisos"]:
        permisos_de_rol[fila["rol_codigo"]].add(fila["permiso_codigo"])
    for rol, permisos in permisos_de_rol.items():
        for regla in dataset["tablas"]["reglas_sod"]:
            assert not (
                regla["permiso_a"] in permisos and regla["permiso_b"] in permisos
            ), f"{rol} infringe {regla['codigo']}"
