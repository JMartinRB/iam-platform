"""Crea el esquema de la base si todavia no existe.

    python -m herramientas.preparar_base

Existe para el arranque local, donde la base es un archivo SQLite que se
crea vacio y hay que poblarlo de tablas antes del primer pedido. Contra
PostgreSQL el camino correcto sigue siendo Alembic, que es el que lleva el
historial de migraciones; este comando crea el esquema tal como esta hoy y
no sabe nada de versiones anteriores.

Es idempotente: ``create_all`` no toca las tablas que ya existen.
"""
from __future__ import annotations

from sqlalchemy import inspect

# La importacion registra las catorce entidades en el metadata. Sin esto,
# `Base.metadata` esta vacio y create_all no crea nada, silenciosamente.
from app.nucleo import modelos  # noqa: F401
from app.nucleo.base_datos import Base, obtener_motor


def preparar() -> list[str]:
    """Crea las tablas que falten y devuelve los nombres de las creadas."""
    motor = obtener_motor()
    antes = set(inspect(motor).get_table_names())
    Base.metadata.create_all(motor)
    despues = set(inspect(motor).get_table_names())
    return sorted(despues - antes)


def main() -> None:
    motor = obtener_motor()
    creadas = preparar()
    print(f"Base: {motor.url}")
    if creadas:
        print(f"Tablas creadas ({len(creadas)}): {', '.join(creadas)}")
    else:
        print("El esquema ya estaba completo, no hubo cambios.")


if __name__ == "__main__":
    main()
