"""Por que fallaron las 200 generaciones del I-04.

La medicion conto 199 errores de proveedor y ningun rechazo de la cadena de
validacion, con una latencia de medio segundo. Eso no es un modelo que genera
mal: es un modelo que no llego a generar. Este script hace una sola llamada y
muestra el motivo exacto, que la medicion se tragaba.

No imprime la clave en ningun momento.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import httpx

RAIZ = pathlib.Path(__file__).resolve().parent
ENV = RAIZ / ".env"

if not ENV.exists():
    sys.exit("No encontre el archivo .env al lado de este script.")

valores: dict[str, str] = {}
for linea in ENV.read_text(encoding="utf-8").splitlines():
    linea = linea.strip()
    if not linea or linea.startswith("#") or "=" not in linea:
        continue
    k, _, v = linea.partition("=")
    valores[k.strip()] = v.strip()

clave = valores.get("LLM_API_KEY", "")
modelo = valores.get("LLM_MODELO", "")
proveedor = valores.get("LLM_PROVEEDOR", "")

print("=" * 60)
print(" Diagnostico del proveedor de modelo")
print("=" * 60)
print(f" proveedor : {proveedor}")
print(f" modelo    : {modelo}")
print(f" clave     : {len(clave)} caracteres"
      f"{' (parece vacia)' if len(clave) < 10 else ''}")
print()

if not clave:
    sys.exit("No hay clave cargada en LLM_API_KEY.")

cabeceras = {"x-goog-api-key": clave}

# ---------------------------------------------------------------- 1. modelos
print("-" * 60)
print(" 1. Modelos que acepta esta clave")
print("-" * 60)
try:
    r = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers=cabeceras,
        timeout=30,
    )
    print(f" HTTP {r.status_code}")
    if r.status_code != 200:
        print(" respuesta:", r.text[:800])
    else:
        disponibles = []
        for m in r.json().get("models", []):
            nombre = m.get("name", "").removeprefix("models/")
            if "generateContent" in m.get("supportedGenerationMethods", []):
                disponibles.append(nombre)
        print(f" {len(disponibles)} modelos sirven para generar. Los flash:")
        for n in sorted(disponibles):
            if "flash" in n:
                print("   ", n)
        print()
        if modelo in disponibles:
            print(f" >>> '{modelo}' ESTA en la lista.")
        else:
            print(f" >>> '{modelo}' NO esta en la lista. Ese es el problema:")
            print("     el .env apunta a un modelo que esta clave no puede usar.")
except Exception as error:  # noqa: BLE001
    print(" fallo la consulta:", error)

# ------------------------------------------------------------ 2. generacion
print()
print("-" * 60)
print(" 2. Una sola generacion de prueba")
print("-" * 60)
cuerpo = {
    "systemInstruction": {"parts": [{"text": "Respondes solo con JSON valido."}]},
    "contents": [{"role": "user", "parts": [{"text": 'Devolve {"ok": true}'}]}],
    "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
}
try:
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
        headers=cabeceras,
        json=cuerpo,
        timeout=60,
    )
    print(f" HTTP {r.status_code}")
    if r.status_code == 200:
        datos = r.json()
        texto = datos["candidates"][0]["content"]["parts"][0]["text"]
        print(" el modelo respondio:", texto.strip()[:200])
        print()
        print(" >>> La llamada funciona. Entonces el problema de la medicion")
        print("     esta en otro lado y hay que mirar el detalle.")
    else:
        print(" respuesta completa del servidor:")
        try:
            print(json.dumps(r.json(), indent=2, ensure_ascii=False)[:1500])
        except Exception:  # noqa: BLE001
            print(r.text[:1500])
except Exception as error:  # noqa: BLE001
    print(" fallo la llamada:", type(error).__name__, error)

print()
print("=" * 60)
print(" Copiale a Claude todo lo que dice esta ventana.")
print(" No contiene la clave.")
print("=" * 60)
