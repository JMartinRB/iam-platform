"""Indicador I-04: validez de los scripts generados.

Mide cuantas salidas del modelo superan la cadena de validacion sin
necesidad de regenerar. Es el unico indicador que no se puede medir con el
proveedor simulado: una salida construida por el propio sistema pasaria
siempre, y lo que estariamos midiendo es este codigo y no un modelo.

Para correrlo hace falta una clave de API del proveedor, configurada en el
archivo .env local. La clave no se pasa por parametro ni se escribe en
ningun archivo versionado.

    LLM_PROVEEDOR=gemini
    LLM_API_KEY=...
    LLM_MODELO=gemini-flash-latest

Uso:
    python -m herramientas.medir_generacion --casos 100
    python -m herramientas.medir_generacion --casos 20 --plataformas powershell

El script arma los casos a partir de un dataset sintetico: resuelve altas
reales con el motor de politicas y le pide al modelo que las escriba. Es
decir, mide sobre los mismos datos que el resto del plan de pruebas, no
sobre ejemplos elegidos para que salgan bien.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import tempfile
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modulos.generacion import generar_script
from app.modulos.importacion import importar_dataset
from app.modulos.politicas import resolucion_a_permisos, resolver_alta
from app.modulos.proveedores import ErrorProveedor, obtener_proveedor
from app.nucleo.base_datos import Base, crear_motor
from app.nucleo.enums import EstadoUsuario
from app.nucleo.modelos import Usuario
from herramientas.generar_dataset import generar

FECHA_CORTE = date(2026, 10, 1)
UMBRAL_I04 = 0.80


def preparar(url: str, carpeta: Path) -> tuple[Session, Any]:
    """Genera el dataset chico y lo carga en una base limpia."""
    generar("D-1", carpeta, fecha_corte=FECHA_CORTE)
    motor = crear_motor(url)
    Base.metadata.drop_all(motor)
    Base.metadata.create_all(motor)
    sesion = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)()
    importar_dataset(sesion, carpeta / "D-1")
    sesion.commit()
    return sesion, motor


def armar_casos(sesion: Session, cantidad: int, semilla: int) -> list[tuple]:
    """Construye casos de alta con cambios concretos para generar.

    Se descartan las resoluciones que no proponen ningun cambio: pedirle a
    un modelo que escriba un script vacio no mide nada.
    """
    rnd = random.Random(semilla)
    usuarios = list(
        sesion.scalars(
            select(Usuario).where(Usuario.estado == EstadoUsuario.ACTIVO)
        ).all()
    )
    rnd.shuffle(usuarios)

    casos: list[tuple[Usuario, list]] = []
    for usuario in usuarios:
        if len(casos) >= cantidad:
            break
        resolucion = resolver_alta(
            sesion, usuario, fecha_corte=FECHA_CORTE, conservar_vigentes=False
        )
        cambios = resolucion_a_permisos(resolucion)
        if cambios:
            casos.append((usuario, cambios))
    return casos


def medir(
    sesion: Session,
    plataformas: list[str],
    casos: int,
    proveedor_nombre: str | None,
    pausa: float,
    semilla: int,
) -> dict[str, Any]:
    proveedor = obtener_proveedor(proveedor_nombre)
    lote = armar_casos(sesion, casos, semilla)
    if not lote:
        raise RuntimeError("el dataset no produjo ningun caso con cambios")

    resultados: dict[str, Any] = {
        "proveedor": proveedor.nombre,
        "modelo": proveedor.modelo,
        "casos_por_plataforma": len(lote),
        "umbral": UMBRAL_I04,
        "plataformas": {},
    }

    for plataforma in plataformas:
        limpias = 0
        aprobadas = 0
        errores = 0
        intentos: list[int] = []
        latencias: list[float] = []
        motivos: Counter[str] = Counter()

        for indice, (usuario, cambios) in enumerate(lote, start=1):
            inicio = time.perf_counter()
            try:
                resultado = generar_script(
                    cambios, usuario, plataforma=plataforma, proveedor=proveedor
                )
            except ErrorProveedor as error:
                errores += 1
                motivos[f"proveedor: {error}"] += 1
                continue
            latencias.append(time.perf_counter() - inicio)
            intentos.append(resultado.intentos)

            if resultado.aprobado:
                aprobadas += 1
                if resultado.sin_regenerar:
                    limpias += 1
            if resultado.error:
                errores += 1

            # El primer control que fallo en el primer intento es lo que
            # explica por que hubo que regenerar.
            primero = resultado.historial[0] if resultado.historial else None
            if primero and not primero["aprobado"]:
                fallado = next(
                    (c for c in primero["controles"] if not c["aprobado"]), None
                )
                if fallado:
                    motivos[fallado["nombre"]] += 1

            if pausa and indice < len(lote):
                time.sleep(pausa)

        total = len(lote)
        resultados["plataformas"][plataforma] = {
            "generaciones": total,
            "validas_sin_regenerar": limpias,
            "validas_tras_reintentar": aprobadas,
            "errores_de_proveedor": errores,
            "tasa_sin_regenerar": round(limpias / total, 4) if total else 0.0,
            "tasa_final": round(aprobadas / total, 4) if total else 0.0,
            "intentos_promedio": round(statistics.mean(intentos), 2) if intentos else 0,
            "latencia_mediana_s": (
                round(statistics.median(latencias), 3) if latencias else 0
            ),
            "motivos_de_rechazo": dict(motivos.most_common()),
        }

    return resultados


def imprimir(datos: dict[str, Any]) -> None:
    print(f"\nProveedor: {datos['proveedor']} ({datos['modelo']})")
    print(f"Casos por plataforma: {datos['casos_por_plataforma']}")
    for plataforma, m in datos["plataformas"].items():
        estado = "CUMPLE" if m["tasa_sin_regenerar"] > datos["umbral"] else "NO CUMPLE"
        print(f"\n=== {plataforma} ===")
        print(f"  validas al primer intento : {m['validas_sin_regenerar']}/"
              f"{m['generaciones']}  ({m['tasa_sin_regenerar']:.1%})  {estado}")
        print(f"  validas tras reintentar   : {m['validas_tras_reintentar']}/"
              f"{m['generaciones']}  ({m['tasa_final']:.1%})")
        print(f"  intentos promedio         : {m['intentos_promedio']}")
        print(f"  latencia mediana          : {m['latencia_mediana_s']} s")
        if m["motivos_de_rechazo"]:
            print("  control que fallo primero:")
            for motivo, cantidad in m["motivos_de_rechazo"].items():
                print(f"    {motivo:<24}{cantidad}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casos", type=int, default=100)
    parser.add_argument(
        "--plataformas", nargs="*", default=["powershell", "okta"]
    )
    parser.add_argument(
        "--proveedor",
        default=None,
        help="gemini | groq | ollama | stub (por omision, el de .env)",
    )
    parser.add_argument(
        "--pausa",
        type=float,
        default=4.5,
        help="segundos entre llamadas, para no exceder el limite del plan gratuito",
    )
    parser.add_argument("--semilla", type=int, default=20260401)
    parser.add_argument("--salida", default="medicion_generacion.json")
    args = parser.parse_args()

    # ignore_cleanup_errors: en Windows el archivo de la base sigue tomado
    # un instante despues de cerrarlo, y perder el directorio temporal no
    # deberia tirar abajo una medicion que ya termino.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        carpeta = Path(tmp)
        sesion, motor = preparar(f"sqlite+pysqlite:///{carpeta / 'i04.db'}", carpeta)
        try:
            datos = medir(
                sesion,
                args.plataformas,
                args.casos,
                args.proveedor,
                args.pausa,
                args.semilla,
            )
        finally:
            sesion.close()
            motor.dispose()

    imprimir(datos)
    Path(args.salida).write_text(
        json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDetalle completo en {args.salida}")


if __name__ == "__main__":
    main()
