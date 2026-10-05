"""Ejecucion del plan de pruebas: mide los indicadores sobre los tres conjuntos.

Produce los valores de los indicadores que el modulo de analisis puede
sostener hoy:

* I-01, tiempo de procesamiento: diez corridas del analisis sobre D-3, con
  mediana y percentil 95, mas el tiempo de carga medido aparte. Se separan
  porque son dos operaciones distintas para el usuario y se optimizan por
  caminos distintos.
* I-02, hallazgos detectados: precision y exhaustividad por categoria sobre
  D-2, contra la verdad de referencia del manifiesto.
* I-05, consistencia: tres corridas del mismo analisis, comparando el
  conjunto completo de hallazgos y no solo la cantidad.

Los indicadores I-03 y I-04 dependen del motor de politicas y de la capa de
generacion, que son modulos posteriores, y por eso no figuran aca.

Uso:
    python -m herramientas.medir
    python -m herramientas.medir --url postgresql+psycopg://iam@/iam \
        --salida medicion.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.modulos.analisis import ParametrosAnalisis, analizar, resumen
from app.modulos.importacion import importar_dataset
from app.nucleo.base_datos import Base, crear_motor
from app.nucleo.modelos import Hallazgo
from herramientas.generar_dataset import PERFILES, generar

FECHA_CORTE = date(2026, 10, 1)
CORRIDAS_RENDIMIENTO = 10
CORRIDAS_CONSISTENCIA = 3


def _firma(hallazgos: list[Hallazgo]) -> set[tuple]:
    """Identidad de un conjunto de hallazgos, para comparar entre corridas."""
    return {
        (h.categoria, h.evidencia["legajo"], h.severidad, float(h.puntaje))
        for h in hallazgos
    }


def _metricas(detectado: set, plantado: set) -> dict[str, float]:
    verdaderos = len(detectado & plantado)
    return {
        "detectados": len(detectado),
        "plantados": len(plantado),
        "verdaderos_positivos": verdaderos,
        "falsos_positivos": len(detectado - plantado),
        "falsos_negativos": len(plantado - detectado),
        "precision": round(verdaderos / len(detectado), 4) if detectado else 0.0,
        "exhaustividad": round(verdaderos / len(plantado), 4) if plantado else 0.0,
    }


def _preparar(url: str) -> tuple[Any, sessionmaker]:
    motor = crear_motor(url)
    with motor.begin() as conexion:
        if motor.dialect.name == "postgresql":
            conexion.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    if motor.dialect.name != "postgresql":
        Base.metadata.drop_all(motor)
    Base.metadata.create_all(motor)
    return motor, sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)


def medir_perfil(perfil_id: str, carpeta: Path, url: str) -> dict[str, Any]:
    """Carga y analiza un conjunto, devolviendo tiempos y metricas."""
    manifiesto = generar(perfil_id, carpeta, fecha_corte=FECHA_CORTE)
    motor, fabrica = _preparar(url)
    parametros = ParametrosAnalisis(fecha_corte=FECHA_CORTE)

    sesion: Session = fabrica()
    inicio = time.perf_counter()
    importacion = importar_dataset(sesion, carpeta / perfil_id)
    sesion.commit()
    tiempo_carga = time.perf_counter() - inicio

    inicio = time.perf_counter()
    hallazgos = analizar(sesion, importacion.snapshot_id, parametros)
    sesion.commit()
    tiempo_analisis = time.perf_counter() - inicio

    datos: dict[str, Any] = {
        "perfil": perfil_id,
        "volumen": manifiesto["volumen"],
        "importacion": {
            "segundos": round(tiempo_carga, 3),
            "filas_leidas": importacion.leidas,
            "filas_aceptadas": importacion.aceptadas,
            "filas_descartadas": importacion.descartadas,
            "motivos": importacion.motivos(),
        },
        "analisis": {
            "segundos": round(tiempo_analisis, 3),
            **resumen(hallazgos, importacion.conteos["usuarios"]),
        },
        "i02": _por_categoria(hallazgos, manifiesto),
    }

    if perfil_id == "D-3":
        datos["i01"] = _rendimiento(sesion, importacion.snapshot_id, parametros)
    if perfil_id == "D-2":
        datos["i05"] = _consistencia(sesion, importacion.snapshot_id, parametros)

    sesion.close()
    motor.dispose()
    return datos


def _por_categoria(hallazgos: list[Hallazgo], manifiesto: dict) -> dict[str, Any]:
    """Indicador I-02, abierto por categoria y consolidado."""
    por_categoria: dict[str, Any] = {}
    detectado_total: set[tuple[str, str]] = set()
    plantado_total: set[tuple[str, str]] = set()

    for categoria, datos in manifiesto["hallazgos_plantados"].items():
        detectado = {
            h.evidencia["legajo"] for h in hallazgos if h.categoria == categoria
        }
        plantado = {caso["legajo"] for caso in datos["casos"]}
        por_categoria[categoria] = _metricas(detectado, plantado)
        detectado_total |= {(categoria, x) for x in detectado}
        plantado_total |= {(categoria, x) for x in plantado}

    # El conflicto de SoD se informa ademas por par infringido, que es la
    # unidad en que efectivamente se remedia.
    pares_emitidos = {
        (h.evidencia["legajo"], h.evidencia["regla"])
        for h in hallazgos
        if h.categoria == "conflicto_sod"
    }
    pares_declarados = {
        (caso["legajo"], codigo)
        for caso in manifiesto["hallazgos_plantados"]["conflicto_sod"]["casos"]
        for codigo in caso["reglas_infringidas"]
    }

    return {
        "por_categoria": por_categoria,
        "consolidado": _metricas(detectado_total, plantado_total),
        "conflicto_sod_por_par": _metricas(pares_emitidos, pares_declarados),
    }


def _rendimiento(
    sesion: Session, snapshot_id: int, parametros: ParametrosAnalisis
) -> dict[str, Any]:
    """Indicador I-01 sobre el conjunto grande."""
    tiempos = []
    for _ in range(CORRIDAS_RENDIMIENTO):
        inicio = time.perf_counter()
        analizar(sesion, snapshot_id, parametros)
        tiempos.append(time.perf_counter() - inicio)
        sesion.rollback()
    ordenados = sorted(tiempos)
    indice = min(int(len(ordenados) * 0.95), len(ordenados) - 1)
    return {
        "corridas": CORRIDAS_RENDIMIENTO,
        "mediana_segundos": round(statistics.median(tiempos), 3),
        "p95_segundos": round(ordenados[indice], 3),
        "minimo_segundos": round(min(tiempos), 3),
        "maximo_segundos": round(max(tiempos), 3),
        "umbral_segundos": 60,
    }


def _consistencia(
    sesion: Session, snapshot_id: int, parametros: ParametrosAnalisis
) -> dict[str, Any]:
    """Indicador I-05: el mismo analisis tiene que dar el mismo resultado."""
    firmas = []
    for _ in range(CORRIDAS_CONSISTENCIA):
        firmas.append(_firma(analizar(sesion, snapshot_id, parametros)))
        sesion.rollback()
    iguales = all(f == firmas[0] for f in firmas)
    diferencias = max(len(f ^ firmas[0]) for f in firmas)
    return {
        "corridas": CORRIDAS_CONSISTENCIA,
        "identicas": iguales,
        "hallazgos_por_corrida": [len(f) for f in firmas],
        "diferencias": diferencias,
    }


def imprimir(resultados: list[dict[str, Any]]) -> None:
    for datos in resultados:
        v = datos["volumen"]
        print(f"\n=== {datos['perfil']} — {v['usuarios']} usuarios, "
              f"{v['asignaciones']} asignaciones ===")
        print(f"  carga    : {datos['importacion']['segundos']:>7.3f} s  "
              f"({datos['importacion']['filas_aceptadas']} filas, "
              f"{datos['importacion']['filas_descartadas']} rechazadas)")
        print(f"  analisis : {datos['analisis']['segundos']:>7.3f} s  "
              f"({datos['analisis']['hallazgos']} hallazgos)")

        print("  I-02 por categoria:")
        print(f"    {'categoria':<20}{'det':>5}{'plant':>7}{'FP':>5}{'FN':>5}"
              f"{'prec':>8}{'exh':>8}")
        for categoria, m in datos["i02"]["por_categoria"].items():
            print(f"    {categoria:<20}{m['detectados']:>5}{m['plantados']:>7}"
                  f"{m['falsos_positivos']:>5}{m['falsos_negativos']:>5}"
                  f"{m['precision']:>8.3f}{m['exhaustividad']:>8.3f}")
        c = datos["i02"]["consolidado"]
        print(f"    {'CONSOLIDADO':<20}{c['detectados']:>5}{c['plantados']:>7}"
              f"{c['falsos_positivos']:>5}{c['falsos_negativos']:>5}"
              f"{c['precision']:>8.3f}{c['exhaustividad']:>8.3f}")

        if "i01" in datos:
            i = datos["i01"]
            print(f"  I-01: mediana {i['mediana_segundos']} s, "
                  f"p95 {i['p95_segundos']} s (umbral {i['umbral_segundos']} s)")
        if "i05" in datos:
            i = datos["i05"]
            print(f"  I-05: {i['corridas']} corridas identicas: {i['identicas']}, "
                  f"diferencias: {i['diferencias']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="sqlite+pysqlite:///medicion.db",
        help="URL de la base donde correr la medicion",
    )
    parser.add_argument("--salida", default="medicion.json")
    parser.add_argument("--perfiles", nargs="*", default=sorted(PERFILES))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        carpeta = Path(tmp)
        resultados = [
            medir_perfil(perfil_id, carpeta, args.url) for perfil_id in args.perfiles
        ]

    imprimir(resultados)
    salida = {
        "fecha_corte": FECHA_CORTE.isoformat(),
        "motor": args.url.split("://")[0],
        "resultados": resultados,
    }
    Path(args.salida).write_text(
        json.dumps(salida, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDetalle completo en {args.salida}")


if __name__ == "__main__":
    main()
