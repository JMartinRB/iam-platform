"""Configuración de la aplicación, leída de variables de entorno."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# El .env vive en la raíz del repositorio, que es donde lo deja el script
# que configura la clave y donde está el .env.example que lo documenta.
#
# La ruta se resuelve de forma absoluta a partir de la ubicación de este
# archivo, y no relativa al directorio desde el que se ejecuta el proceso.
# La diferencia no es cosmética: casi todos los comandos del proyecto se
# corren parados en backend/, así que un `.env` relativo apuntaba a
# backend/.env, un archivo que no existe. El efecto era que la clave del
# modelo quedaba sin leer y cualquier llamada al proveedor real fallaba
# diciendo que faltaba la clave, con la clave ahí escrita.
RAIZ = Path(__file__).resolve().parents[3]


class Configuracion(BaseSettings):
    # Se admiten los dos: el de la raíz y, si existe, uno propio de
    # backend/. El segundo pisa al primero, que es lo que permite ajustar
    # algo puntual sin tocar el archivo compartido.
    model_config = SettingsConfigDict(
        env_file=(RAIZ / ".env", Path(".env")), extra="ignore"
    )

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
