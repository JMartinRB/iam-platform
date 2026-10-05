"""Punto de entrada de la API.

Por ahora expone solo lo necesario para verificar que el esqueleto corre de
punta a punta: estado del servicio y conectividad con la base. Los routers
de carga, analisis, politicas y generacion se montan en los proximos
modulos; se dejan declarados en el orden en que van a aparecer para que la
estructura del archivo no cambie cada vez.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.nucleo.base_datos import obtener_sesion
from app.nucleo.config import obtener_config

VERSION = "0.1.0"

app = FastAPI(
    title="Plataforma de analisis y remediacion de accesos",
    version=VERSION,
    description=(
        "API de la plataforma de analisis, asignacion y remediacion de "
        "accesos en entornos IAM."
    ),
)


@app.get("/salud", tags=["servicio"])
def salud(sesion: Session = Depends(obtener_sesion)) -> dict:
    """Estado del servicio y de la base.

    El chequeo de base se informa pero no tumba el endpoint: en desarrollo
    es util poder levantar la API antes de que el contenedor de PostgreSQL
    termine de iniciar.
    """
    try:
        sesion.execute(text("SELECT 1"))
        base = "ok"
    except Exception as error:
        base = f"sin conexion: {type(error).__name__}"

    config = obtener_config()
    return {
        "estado": "ok",
        "version": VERSION,
        "base_datos": base,
        "proveedor_llm": config.llm_proveedor,
    }
