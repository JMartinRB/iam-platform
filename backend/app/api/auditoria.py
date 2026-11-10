"""Registro de auditoría.

Es de solo lectura a propósito: la API no expone ninguna forma de escribir
ni de borrar un evento. Los eventos los escriben los módulos cuando hacen
algo, y nada en la aplicación los modifica después.

El entrevistado del apéndice F pidió que el registro fuera inalterable. Hoy
es una tabla común y una escritura directa contra la base podría tocarla;
cerrarlo de verdad requiere almacenamiento append-only o encadenamiento de
hashes, y quedó declarado como trabajo futuro.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencias import sesion_db
from app.api.esquemas import EventoSalida
from app.nucleo.modelos import EventoAuditoria

router = APIRouter(prefix="/api/auditoria", tags=["auditoria"])


@router.get("", response_model=list[EventoSalida])
def listar(
    entidad: str | None = None,
    actor: str | None = None,
    limite: int = 200,
    sesion: Session = Depends(sesion_db),
) -> list[EventoSalida]:
    """Eventos registrados, del más reciente al más viejo."""
    consulta = select(EventoAuditoria).order_by(EventoAuditoria.id.desc())
    if entidad:
        consulta = consulta.where(EventoAuditoria.entidad == entidad)
    if actor:
        consulta = consulta.where(EventoAuditoria.actor == actor)

    return [
        EventoSalida(
            id=e.id,
            actor=e.actor,
            accion=e.accion,
            entidad=e.entidad,
            entidad_id=e.entidad_id,
            detalle=e.detalle or {},
            ocurrido_en=e.ocurrido_en,
        )
        for e in sesion.scalars(consulta.limit(limite)).all()
    ]
