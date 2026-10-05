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
    # Múltiplo de la mediana de permisos del puesto a partir del cual se
    # considera sobreprivilegio. Se usa la mediana y no un percentil porque
    # en un grupo chico los propios casos atípicos corren el percentil.
    factor_sobreprivilegio: float = 2.0
    minimo_pares_sobreprivilegio: int = 5
    antiguedad_creep_dias: int = 365


@lru_cache
def obtener_config() -> Configuracion:
    return Configuracion()
