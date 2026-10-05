"""Accesorios compartidos por las pruebas."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.nucleo.base_datos import Base, crear_motor
from herramientas.generar_dataset import generar

FECHA_CORTE = date(2026, 10, 1)


@pytest.fixture()
def sesion() -> Session:
    """Base SQLite en memoria con el esquema completo creado."""
    motor = crear_motor("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(motor)
    fabrica = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)
    with fabrica() as s:
        yield s
    Base.metadata.drop_all(motor)
    motor.dispose()


@pytest.fixture(scope="session")
def dataset_d1(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Genera D-1 una sola vez y devuelve sus rutas, manifiesto y tablas."""
    destino = tmp_path_factory.mktemp("datasets")
    manifiesto = generar("D-1", destino, fecha_corte=FECHA_CORTE)
    carpeta = destino / "D-1"
    return {
        "carpeta": carpeta,
        "manifiesto": manifiesto,
        "tablas": {
            nombre: leer_csv(carpeta / f"{nombre}.csv")
            for nombre in (
                "aplicaciones",
                "permisos",
                "roles",
                "roles_permisos",
                "usuarios",
                "asignaciones",
                "reglas_sod",
            )
        },
    }


def leer_csv(ruta: Path) -> list[dict[str, str]]:
    with ruta.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def leer_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))
