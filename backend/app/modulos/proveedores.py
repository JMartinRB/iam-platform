"""Adaptadores de proveedores de modelo de lenguaje.

Todos los proveedores se usan a traves de la misma interfaz, que recibe dos
mensajes —el del sistema, fijo, y el del usuario, con el contexto— y
devuelve texto. Esa uniformidad es lo que permite cambiar de proveedor sin
tocar el resto del sistema, y lo que hace que el modelo local sirva de
respaldo real cuando no hay conexion.

El proveedor por omision es el simulado. No es un adaptador de mentira: es
el que permite que la suite de pruebas ejercite la cadena de validacion
completa sin depender de un servicio externo, sin consumir cuota y sin que
el resultado de una prueba cambie porque el proveedor cambio de version.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.nucleo.config import obtener_config

TIEMPO_LIMITE = 60.0


@dataclass
class Respuesta:
    texto: str
    proveedor: str
    modelo: str
    intento: int = 1


class ErrorProveedor(RuntimeError):
    """El proveedor no pudo responder."""


class Proveedor(Protocol):
    nombre: str
    modelo: str

    def generar(self, sistema: str, pedido: str) -> Respuesta: ...


# ---------------------------------------------------------------------------
# Simulado
# ---------------------------------------------------------------------------


class ProveedorSimulado:
    """Arma la respuesta a partir del propio contexto, sin inventar nada.

    Sirve para dos cosas: que el sistema se pueda levantar y recorrer de
    punta a punta sin credenciales, y que las pruebas de la cadena de
    validacion tengan una entrada estable. Lo que no sirve es para medir la
    calidad de las salidas, porque mediria este codigo y no un modelo.
    """

    nombre = "stub"

    def __init__(self, modelo: str = "simulado") -> None:
        self.modelo = modelo

    def generar(self, sistema: str, pedido: str) -> Respuesta:
        contexto = _leer_contexto(pedido)
        plataforma = contexto.get("plataforma", "powershell")
        usuario = contexto.get("usuario", "ID-DESCONOCIDO")
        cambios = contexto.get("cambios", [])

        if plataforma == "okta":
            script = json.dumps(
                [
                    {
                        "metodo": "PUT",
                        "ruta": f"/api/v1/groups/{c['grupo']}/users/{usuario}",
                        "grupo": c["grupo"],
                        "usuario": usuario,
                    }
                    for c in cambios
                ],
                indent=2,
            )
        else:
            lineas = [
                f"Add-ADGroupMember -Identity {c['grupo']} -Members {usuario}"
                if c["accion"] == "otorgar"
                else f"Remove-ADGroupMember -Identity {c['grupo']} -Members {usuario} "
                f"-Confirm:$false"
                for c in cambios
            ]
            script = "\n".join(lineas)

        return Respuesta(
            texto=json.dumps(
                {
                    "lenguaje": plataforma,
                    "script": script,
                    "explicacion": (
                        f"{len(cambios)} cambios de pertenencia a grupo para "
                        f"{usuario}."
                    ),
                },
                ensure_ascii=False,
            ),
            proveedor=self.nombre,
            modelo=self.modelo,
        )


def _leer_contexto(pedido: str) -> dict[str, Any]:
    """Extrae el bloque de datos delimitado del pedido."""
    inicio = pedido.find("<datos>")
    fin = pedido.find("</datos>")
    if inicio == -1 or fin == -1:
        return {}
    try:
        return json.loads(pedido[inicio + len("<datos>") : fin])
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Proveedores reales
# ---------------------------------------------------------------------------


class ProveedorGemini:
    """API de Google AI Studio."""

    nombre = "gemini"
    url = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, clave: str, modelo: str) -> None:
        if not clave:
            raise ErrorProveedor("falta LLM_API_KEY para el proveedor gemini")
        self.clave = clave
        self.modelo = modelo

    def generar(self, sistema: str, pedido: str) -> Respuesta:
        cuerpo = {
            "systemInstruction": {"parts": [{"text": sistema}]},
            "contents": [{"role": "user", "parts": [{"text": pedido}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        try:
            respuesta = httpx.post(
                f"{self.url}/{self.modelo}:generateContent",
                headers={"x-goog-api-key": self.clave},
                json=cuerpo,
                timeout=TIEMPO_LIMITE,
            )
            respuesta.raise_for_status()
            datos = respuesta.json()
            texto = datos["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, KeyError, IndexError) as error:
            raise ErrorProveedor(f"gemini no respondio: {error}") from error
        return Respuesta(texto=texto, proveedor=self.nombre, modelo=self.modelo)


class ProveedorGroq:
    """API de Groq, compatible con el formato de OpenAI."""

    nombre = "groq"
    url = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, clave: str, modelo: str) -> None:
        if not clave:
            raise ErrorProveedor("falta LLM_API_KEY para el proveedor groq")
        self.clave = clave
        self.modelo = modelo

    def generar(self, sistema: str, pedido: str) -> Respuesta:
        try:
            respuesta = httpx.post(
                self.url,
                headers={"Authorization": f"Bearer {self.clave}"},
                json={
                    "model": self.modelo,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": sistema},
                        {"role": "user", "content": pedido},
                    ],
                },
                timeout=TIEMPO_LIMITE,
            )
            respuesta.raise_for_status()
            texto = respuesta.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as error:
            raise ErrorProveedor(f"groq no respondio: {error}") from error
        return Respuesta(texto=texto, proveedor=self.nombre, modelo=self.modelo)


class ProveedorOllama:
    """Modelo local. Es el respaldo cuando no hay conexion ni cuota.

    Funciona como red, no como equivalente: las salidas de un modelo chico
    corriendo en una maquina de escritorio son perceptiblemente mas pobres
    que las de la API remota, y eso se ve en la tasa de regeneracion.
    """

    nombre = "ollama"

    def __init__(self, modelo: str, url: str = "http://localhost:11434") -> None:
        self.modelo = modelo
        self.url = url

    def generar(self, sistema: str, pedido: str) -> Respuesta:
        try:
            respuesta = httpx.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.modelo,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1},
                    "messages": [
                        {"role": "system", "content": sistema},
                        {"role": "user", "content": pedido},
                    ],
                },
                timeout=TIEMPO_LIMITE * 3,
            )
            respuesta.raise_for_status()
            texto = respuesta.json()["message"]["content"]
        except (httpx.HTTPError, KeyError) as error:
            raise ErrorProveedor(f"ollama no respondio: {error}") from error
        return Respuesta(texto=texto, proveedor=self.nombre, modelo=self.modelo)


def obtener_proveedor(nombre: str | None = None) -> Proveedor:
    """Devuelve el proveedor configurado."""
    config = obtener_config()
    nombre = (nombre or config.llm_proveedor).lower()

    if nombre == "gemini":
        return ProveedorGemini(config.llm_api_key, config.llm_modelo)
    if nombre == "groq":
        return ProveedorGroq(config.llm_api_key, config.llm_modelo)
    if nombre == "ollama":
        return ProveedorOllama(config.llm_modelo)
    if nombre == "stub":
        return ProveedorSimulado()
    raise ErrorProveedor(f"proveedor desconocido: {nombre}")
