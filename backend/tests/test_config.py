"""Pruebas de la configuración.

La que importa es la primera: que el ``.env`` de la raíz se lea aunque el
proceso se ejecute parado en ``backend/``. Casi todos los comandos del
proyecto se corren desde ahí, y mientras la ruta fue relativa la clave del
modelo quedaba sin leer. El síntoma era desconcertante —el sistema decía
que faltaba la clave con la clave escrita en el archivo— así que conviene
que haya una prueba que lo fije.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.nucleo.config import RAIZ, Configuracion, obtener_config

BACKEND = Path(__file__).resolve().parents[1]


def test_la_raiz_apunta_al_repositorio() -> None:
    """RAIZ tiene que ser la carpeta que contiene backend/ y frontend/."""
    assert (RAIZ / "backend").is_dir()
    assert (RAIZ / ".env.example").is_file()


@pytest.mark.skipif(
    (RAIZ / ".env").exists(),
    reason="hay un .env real en la raíz y la prueba lo sobrescribiría",
)
def test_el_env_de_la_raiz_se_lee_corriendo_desde_backend() -> None:
    """Se ejecuta en un proceso aparte, parado en backend/.

    Hace falta un proceso nuevo porque la configuración está cacheada y
    porque lo que se quiere comprobar es justamente el efecto del
    directorio de trabajo, que no se puede simular dentro de esta misma
    ejecución.
    """
    env = RAIZ / ".env"
    env.write_text(
        "LLM_PROVEEDOR=gemini\nLLM_API_KEY=valor-de-prueba\n", encoding="utf-8"
    )
    # Se quitan del entorno en lugar de vaciarlas: una variable de entorno
    # definida, aunque sea vacía, tiene prioridad sobre el archivo y la
    # prueba estaría midiendo el entorno y no el .env.
    limpio = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {"LLM_PROVEEDOR", "LLM_API_KEY"}
    }
    try:
        salida = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.nucleo.config import obtener_config as c;"
                "k = c(); print(k.llm_proveedor, bool(k.llm_api_key))",
            ],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            check=True,
            env=limpio,
        )
    finally:
        env.unlink(missing_ok=True)

    assert salida.stdout.strip() == "gemini True"


def test_las_variables_de_entorno_tienen_prioridad_sobre_el_archivo(
    monkeypatch,
) -> None:
    """Poder pisar un valor sin editar el archivo compartido."""
    monkeypatch.setitem(os.environ, "LLM_PROVEEDOR", "ollama")
    assert Configuracion().llm_proveedor == "ollama"


def test_los_umbrales_tienen_los_valores_documentados() -> None:
    """Los mismos que la tabla del apéndice de detalles técnicos."""
    obtener_config.cache_clear()
    config = Configuracion(_env_file=None)

    assert config.dias_cuenta_dormida == 90
    assert config.factor_sobreprivilegio == 2.0
    assert config.minimo_pares_sobreprivilegio == 5
    assert config.antiguedad_creep_dias == 365
    assert config.llm_proveedor == "stub"
