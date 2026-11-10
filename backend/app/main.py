"""Punto de entrada de la API y de la interfaz web.

Expone los cuatro grupos de endpoints que consumen las pantallas —carga y
diagnóstico, altas, scripts y auditoría— y sirve el frontend como archivos
estáticos desde la raíz.

El frontend no tiene paso de compilación: es HTML, CSS y JavaScript que el
navegador entiende tal como están en el repositorio. Es una decisión
deliberada para un prototipo académico. Un bundler agregaría una cadena de
herramientas, un árbol de dependencias y un artefacto compilado que nadie
puede leer, a cambio de comodidades que a esta escala no hacen falta. Acá
lo que se lee en el repositorio es exactamente lo que corre en el
navegador, que para un trabajo que va a ser revisado vale más.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import altas, auditoria, scripts, snapshots
from app.nucleo.base_datos import obtener_sesion
from app.nucleo.config import obtener_config

VERSION = "1.0.0"

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    """Prepara el esquema si la base es un archivo SQLite todavía vacío.

    Es una comodidad del arranque local y nada más: sin esto, abrir la
    aplicación recién instalada devuelve un error de tabla inexistente que
    no le dice nada a quien la está probando.

    Contra PostgreSQL no se toca nada. Ahí el esquema lo lleva Alembic, que
    mantiene el historial de migraciones, y crear tablas por fuera de ese
    historial dejaría la base en un estado que ninguna migración describe.
    """
    if obtener_config().database_url.startswith("sqlite"):
        from herramientas.preparar_base import preparar

        creadas = preparar()
        if creadas:
            print(f"[inicio] esquema creado: {len(creadas)} tablas")
    yield


app = FastAPI(
    title="Plataforma de analisis y remediacion de accesos",
    version=VERSION,
    lifespan=ciclo_de_vida,
    description=(
        "API de la plataforma de analisis, asignacion y remediacion de "
        "accesos en entornos IAM."
    ),
)

app.include_router(snapshots.router)
app.include_router(altas.router)
app.include_router(scripts.router)
app.include_router(auditoria.router)


@app.get("/api/salud", tags=["servicio"])
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


# Se mantiene la ruta anterior para no romper nada que ya la use.
@app.get("/salud", tags=["servicio"], include_in_schema=False)
def salud_compat(sesion: Session = Depends(obtener_sesion)) -> dict:
    return salud(sesion)


if FRONTEND.is_dir():

    @app.get("/", include_in_schema=False)
    def inicio() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")

    app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")
