"""Motor de base de datos, sesion y base declarativa.

El motor se crea de forma diferida. Importar este modulo no abre ninguna
conexion ni exige que el driver de PostgreSQL este instalado, lo cual
permite que las pruebas unitarias trabajen contra SQLite en memoria sin
levantar la base real.
"""
from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.nucleo.config import obtener_config


class Base(DeclarativeBase):
    """Base declarativa de todos los modelos."""


@lru_cache(maxsize=1)
def obtener_motor() -> Engine:
    """Devuelve el motor de la aplicacion, creandolo la primera vez."""
    return crear_motor(obtener_config().database_url)


def crear_motor(url: str) -> Engine:
    """Crea un motor para la URL indicada.

    En SQLite las restricciones de clave ajena vienen desactivadas por
    omision, asi que se habilitan al conectar. Sin eso las pruebas que
    verifican la integridad referencial pasarian por el motivo equivocado.
    """
    motor = create_engine(url, echo=False, future=True)
    if motor.dialect.name == "sqlite":

        @event.listens_for(motor, "connect")
        def _activar_fk(conexion, _registro) -> None:  # pragma: no cover
            cursor = conexion.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return motor


@lru_cache(maxsize=1)
def obtener_fabrica_sesiones() -> sessionmaker[Session]:
    return sessionmaker(bind=obtener_motor(), autoflush=False, expire_on_commit=False)


def obtener_sesion() -> Iterator[Session]:
    """Dependencia de FastAPI: una sesion por request."""
    sesion = obtener_fabrica_sesiones()()
    try:
        yield sesion
        sesion.commit()
    except Exception:
        sesion.rollback()
        raise
    finally:
        sesion.close()
