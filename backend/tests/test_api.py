"""Prueba de humo de la API.

Verifica que el esqueleto levante y responda. Es poco, pero es lo que
convierte al proyecto en algo ejecutable desde el primer dia en lugar de un
conjunto de modulos que todavia no corren juntos.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.nucleo.config import obtener_config


@pytest.fixture()
def cliente(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setitem(
        os.environ, "DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'api.db'}"
    )
    obtener_config.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    obtener_config.cache_clear()


def test_salud_responde(cliente: TestClient) -> None:
    respuesta = cliente.get("/salud")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "ok"
    assert cuerpo["base_datos"] == "ok"
    assert cuerpo["version"]


def test_la_documentacion_se_publica(cliente: TestClient) -> None:
    assert cliente.get("/openapi.json").status_code == 200
