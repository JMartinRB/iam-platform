"""Mide el indicador I-04 contra Groq en lugar de Gemini.

La cuota gratuita de Gemini es de 20 generaciones por dia y el protocolo de
la Tabla 7.4 pide cien por plataforma. Groq sirve modelos abiertos con un
limite por minuto en lugar de por dia, asi que la medicion completa entra en
una sola corrida.

Cambiar de proveedor es una linea del .env porque el sistema los tiene
detras de una misma interfaz interna. Esa decision de diseno esta descrita en
el capitulo 4 y es, ademas, la contingencia del riesgo R05.

Dos cosas que la version anterior de este script hacia mal y aca estan
corregidas. La clave se pide sin eco, para que no quede escrita en la
pantalla ni en el historial de la consola. Y el modelo no se elige solo: el
catalogo de Groq trae modelos de audio y modelos entrenados para otros
idiomas, que no sirven para generar scripts, asi que la eleccion se muestra
y se confirma.
"""
from __future__ import annotations

import getpass
import pathlib
import subprocess
import sys

import httpx

RAIZ = pathlib.Path(__file__).resolve().parent
ENV = RAIZ / ".env"

# Modelos de instrucciones de proposito general, de mayor a menor preferencia.
# Para generar PowerShell y llamadas REST conviene el mas grande disponible.
PREFERIDOS = (
    "gpt-oss-120b", "llama-3.3-70b", "llama-3.1-70b", "llama3-70b",
    "qwen3.8-27b", "gpt-oss-20b", "mixtral", "gemma2",
)
# Audio, vision, moderacion y modelos de idioma especifico: no generan codigo.
EXCLUIDOS = (
    "whisper", "tts", "guard", "vision", "embed", "orpheus", "canopylabs",
    "allam", "playai", "distil",
)


def leer_env() -> dict[str, str]:
    valores: dict[str, str] = {}
    if not ENV.exists():
        sys.exit("No encontre el archivo .env al lado de este script.")
    for linea in ENV.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            valores[k.strip()] = v.strip()
    return valores


def escribir_env(cambios: dict[str, str]) -> None:
    lineas = ENV.read_text(encoding="utf-8").splitlines()
    pendientes = dict(cambios)
    salida = []
    for linea in lineas:
        clave = linea.split("=", 1)[0].strip() if "=" in linea else ""
        if clave in pendientes:
            salida.append(f"{clave}={pendientes.pop(clave)}")
        else:
            salida.append(linea)
    for k, v in pendientes.items():
        salida.append(f"{k}={v}")
    ENV.write_text("\n".join(salida) + "\n", encoding="utf-8")


print("=" * 62)
print(" Medicion del indicador I-04 contra Groq")
print("=" * 62)
print()

env = leer_env()
clave = env.get("LLM_API_KEY", "")
proveedor = env.get("LLM_PROVEEDOR", "").lower()

if proveedor != "groq" or not clave or not clave.startswith("gsk_"):
    print(" Hace falta una clave de Groq. Se saca gratis en console.groq.com,")
    print(" en Developers > API Keys. No hace falta tarjeta.")
    print()
    print(" Pegala y presiona Enter. NO se va a ver mientras la pegas:")
    print(" eso es a proposito, para que no quede en la pantalla.")
    print()
    clave = getpass.getpass(" Clave de Groq: ").strip()
    if not clave:
        sys.exit(" No se recibio ninguna clave. No se cambio nada.")
    escribir_env({"LLM_PROVEEDOR": "groq", "LLM_API_KEY": clave})
    print(f" Guardada en .env ({len(clave)} caracteres). El .env no se sube a git.")
    print()

cab = {"Authorization": f"Bearer {clave}"}

print("-" * 62)
print(" 1. Modelos que acepta esta clave")
print("-" * 62)
try:
    r = httpx.get("https://api.groq.com/openai/v1/models", headers=cab, timeout=30)
    if r.status_code != 200:
        sys.exit(f" HTTP {r.status_code}: {r.text[:500]}")
    ids = sorted(m["id"] for m in r.json().get("data", []))
except Exception as error:  # noqa: BLE001
    sys.exit(f" No pude consultar los modelos: {error}")

utiles = [m for m in ids if not any(x in m.lower() for x in EXCLUIDOS)]

if not utiles:
    print(" El catalogo completo de esta clave:")
    for m in ids:
        print("   ", m)
    sys.exit("\n Ninguno sirve para generar codigo. Avisale a Claude.")

sugerido = ""
for pref in PREFERIDOS:
    coincidencias = [m for m in utiles if pref in m.lower()]
    if coincidencias:
        sugerido = coincidencias[0]
        break
if not sugerido:
    sugerido = utiles[0]

print(" Modelos de texto disponibles:")
for i, m in enumerate(utiles, 1):
    marca = "  <-- sugerido" if m == sugerido else ""
    print(f"   {i}. {m}{marca}")
print()
eleccion = input(f" Numero del modelo, o Enter para usar {sugerido}: ").strip()
if eleccion:
    try:
        elegido = utiles[int(eleccion) - 1]
    except (ValueError, IndexError):
        sys.exit(" Eso no es un numero de la lista. No se midio nada.")
else:
    elegido = sugerido

print(f" >>> Se va a medir con: {elegido}")
escribir_env({"LLM_MODELO": elegido})

print()
print("-" * 62)
print(" 2. Una generacion de prueba")
print("-" * 62)
try:
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=cab,
        json={
            "model": elegido,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Respondes solo con JSON valido."},
                {"role": "user", "content": 'Devolve {"ok": true}'},
            ],
        },
        timeout=60,
    )
    if r.status_code != 200:
        print(f" HTTP {r.status_code}")
        print(r.text[:1200])
        sys.exit("\n Copiale esto a Claude. No se midio nada.")
    print(" respondio:", r.json()["choices"][0]["message"]["content"].strip()[:120])
except Exception as error:  # noqa: BLE001
    sys.exit(f" Fallo la llamada: {type(error).__name__}: {error}")

print()
print("-" * 62)
print(" 3. Medicion completa: 100 casos por plataforma")
print("-" * 62)
print(" Son 200 generaciones. Con la pausa configurada tarda unos 7 minutos.")
print(" No cierres la ventana.")
print()

orden = [
    sys.executable, "-m", "herramientas.medir_generacion",
    "--casos", "100", "--proveedor", "groq", "--pausa", "2.0",
    "--salida", str(RAIZ / "medicion_generacion_groq.json"),
]
resultado = subprocess.run(orden, cwd=str(RAIZ / "backend"))

print()
print("=" * 62)
if resultado.returncode == 0:
    print(" Listo. Pasale a Claude el archivo")
    print(" medicion_generacion_groq.json de la carpeta iam-platform,")
    print(" junto con todo lo que dice esta ventana.")
else:
    print(" La medicion termino con error. Copiale a Claude el texto de arriba.")
print("=" * 62)
