"""Configuración de la aplicación, leída de variables de entorno."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///./iam.db"

    # proveedor de modelo de lenguaje: gemini | groq | ollama | stub
    llm_proveedor: str = "stub"
    llm_api_key: str = ""
    llm_modelo: str = "gemini-flash-latest"

    jwt_secret: str = "cambiar-en-produccion"
    jwt_minutos: int = 30

    # umbrales del módulo de análisis
    dias_cuenta_dormida: int = 90
    percentil_sobreprivilegio: int = 95


@lru_cache
def obtener_config() -> Configuracion:
    return Configuracion()
