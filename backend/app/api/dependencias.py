"""Dependencias compartidas por los routers.

La sesion de base se resuelve una sola vez acá para que los routers no
tengan que conocer como se arma, y para poder sustituirla en las pruebas
con un override de FastAPI.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.nucleo.base_datos import obtener_sesion
from app.nucleo.modelos import Snapshot


def sesion_db() -> Iterator[Session]:
    """Sesion por pedido, cerrada al terminar."""
    yield from obtener_sesion()


def snapshot_existente(
    snapshot_id: int, sesion: Session = Depends(sesion_db)
) -> Snapshot:
    """Resuelve el snapshot o devuelve un 404 con un mensaje legible."""
    snapshot = sesion.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no existe el snapshot {snapshot_id}",
        )
    return snapshot


def fecha_de_corte(snapshot: Snapshot) -> date:
    """Fecha contra la que se evalua un snapshot.

    Se usa la fecha de carga y no la del dia, para que volver a analizar un
    snapshot viejo de el mismo resultado que cuando se cargo. Si cambiara
    con el reloj, dos corridas del mismo analisis podrian diferir y el
    indicador de consistencia dejaria de significar algo.
    """
    return snapshot.creado_en.date()
