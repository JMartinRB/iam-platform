"""La migracion tiene que describir el modelo, no una version anterior.

Es el control que evita el problema clasico de dos fuentes de verdad: los
modelos cambian, nadie genera la revision, y la base que se levanta en el
contenedor deja de parecerse a la que usan las pruebas. Esta prueba aplica
la migracion sobre una base vacia y despues compara el resultado contra los
modelos; cualquier diferencia la hace fallar.
"""
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from app.nucleo import modelos  # noqa: F401  registra las tablas
from app.nucleo.base_datos import Base, crear_motor
from app.nucleo.config import obtener_config

RAIZ = Path(__file__).resolve().parent.parent


def test_la_migracion_reproduce_el_modelo(tmp_path, monkeypatch) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'esquema.db'}"
    monkeypatch.setitem(os.environ, "DATABASE_URL", url)
    obtener_config.cache_clear()

    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migraciones"))
    command.upgrade(config, "head")

    motor = crear_motor(url)
    try:
        with motor.connect() as conexion:
            contexto = MigrationContext.configure(conexion)
            diferencias = compare_metadata(contexto, Base.metadata)
    finally:
        motor.dispose()
        obtener_config.cache_clear()

    assert diferencias == [], (
        "la migracion no coincide con los modelos; falta generar una revision"
    )
