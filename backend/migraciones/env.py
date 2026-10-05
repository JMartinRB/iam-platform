"""Entorno de Alembic.

La URL de conexion no se escribe en alembic.ini: se toma de la
configuracion de la aplicacion, que a su vez la lee de la variable de
entorno DATABASE_URL. Asi el mismo archivo sirve en desarrollo, en el
contenedor y en una base de prueba, sin credenciales versionadas.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# importar los modelos registra las tablas en Base.metadata
from app.nucleo import modelos  # noqa: F401
from app.nucleo.base_datos import Base
from app.nucleo.config import obtener_config

config = context.config
config.set_main_option("sqlalchemy.url", obtener_config().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    conectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with conectable.connect() as conexion:
        context.configure(
            connection=conexion,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=conexion.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
