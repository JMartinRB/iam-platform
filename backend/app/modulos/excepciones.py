"""Excepciones justificadas sobre hallazgos (RF-11).

Hay una decision de diseno que conviene dejar explicita, porque es la que
explica por que este modulo existe en lugar de ser tres lineas dentro del
analisis.

El motor de analisis no sabe nada de excepciones. Corre las cinco reglas y
emite todos los hallazgos que encuentra, siempre. Las excepciones se
aplican despues, como una capa de anotacion sobre esos hallazgos.

La razon no es estetica. El indicador I-02 mide la precision y la
exhaustividad del motor contra una verdad de referencia conocida, y lo hace
comparando el conjunto de hallazgos devueltos contra el conjunto plantado.
Si el motor filtrara las excepciones, esa comparacion dejaria de medir al
motor: un hallazgo plantado que alguien marco como excepcion aparecería
como un falso negativo, y la exhaustividad bajaria por una decision
administrativa y no por un error de deteccion. Separar las dos cosas
mantiene cada medicion diciendo lo que dice.

Para el analista el efecto es el que pide el criterio de aceptacion: un
hallazgo exceptuado no vuelve a figurar como riesgo en el diagnostico
siguiente, no suma al indice y queda registrado con su motivo y su
responsable.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nucleo.modelos import (
    EventoAuditoria,
    ExcepcionHallazgo,
    Hallazgo,
    Usuario,
    ahora,
)


class ErrorExcepcion(Exception):
    """La excepcion pedida no se puede registrar."""


@dataclass
class HallazgoAnotado:
    """Un hallazgo junto con la excepcion que lo cubre, si la hay."""

    hallazgo: Hallazgo
    excepcion: ExcepcionHallazgo | None = None

    @property
    def exceptuado(self) -> bool:
        return self.excepcion is not None


def vigentes(sesion: Session) -> list[ExcepcionHallazgo]:
    """Excepciones activas, las unicas que tapan algo."""
    return list(
        sesion.scalars(
            select(ExcepcionHallazgo).where(ExcepcionHallazgo.activa.is_(True))
        ).all()
    )


def anotar(
    hallazgos: list[Hallazgo], excepciones: list[ExcepcionHallazgo]
) -> list[HallazgoAnotado]:
    """Marca cada hallazgo con la excepcion que lo cubre, si existe.

    Funcion pura: no toca la base ni modifica los hallazgos. Eso permite
    probarla sola y deja que quien la llame decida que hacer con el
    resultado, que no siempre es lo mismo: el panel los esconde, el detalle
    los muestra con su justificacion a la vista.
    """
    por_usuario: dict[int, list[ExcepcionHallazgo]] = {}
    for e in excepciones:
        por_usuario.setdefault(e.usuario_id, []).append(e)

    anotados = []
    for h in hallazgos:
        cubre = None
        for e in por_usuario.get(h.usuario_id or -1, []):
            if e.alcanza(str(h.categoria), h.regla_sod_id):
                cubre = e
                break
        anotados.append(HallazgoAnotado(hallazgo=h, excepcion=cubre))
    return anotados


def registrar(
    sesion: Session,
    hallazgo: Hallazgo,
    motivo: str,
    responsable: str,
    solo_esta_regla: bool = True,
) -> ExcepcionHallazgo:
    """Marca un hallazgo como excepcion justificada.

    El motivo es obligatorio y no puede ser una cadena vacia. Una excepcion
    sin justificacion escrita es indistinguible de haber apagado el
    hallazgo porque molestaba, y el valor de todo el mecanismo esta en que
    esa diferencia quede asentada.
    """
    motivo = motivo.strip()
    if not motivo:
        raise ErrorExcepcion("la excepcion necesita un motivo escrito")
    if not responsable.strip():
        raise ErrorExcepcion("la excepcion necesita un responsable")
    if hallazgo.usuario_id is None:
        raise ErrorExcepcion("el hallazgo no esta asociado a un usuario")

    # Solo la categoria de conflictos identifica la regla que se infringio.
    # En las otras cuatro, regla_sod_id es None y el alcance resulta la
    # categoria entera para ese usuario, que es lo unico que el motor
    # permite distinguir.
    regla_id = hallazgo.regla_sod_id if solo_esta_regla else None
    existente = sesion.scalars(
        select(ExcepcionHallazgo).where(
            ExcepcionHallazgo.usuario_id == hallazgo.usuario_id,
            ExcepcionHallazgo.categoria == hallazgo.categoria,
            ExcepcionHallazgo.regla_sod_id == regla_id,
        )
    ).one_or_none()

    if existente is not None:
        if existente.activa:
            raise ErrorExcepcion("ya existe una excepcion activa para ese alcance")
        # Reactivar la anterior en lugar de crear otra mantiene una sola
        # fila por alcance y conserva el historial de la primera.
        existente.activa = True
        existente.motivo = motivo
        existente.responsable = responsable.strip()
        existente.revocada_en = None
        existente.revocada_por = None
        excepcion = existente
    else:
        excepcion = ExcepcionHallazgo(
            usuario_id=hallazgo.usuario_id,
            categoria=str(hallazgo.categoria),
            regla_sod_id=regla_id,
            motivo=motivo,
            responsable=responsable.strip(),
        )
        sesion.add(excepcion)

    sesion.flush()
    usuario = sesion.get(Usuario, hallazgo.usuario_id)
    sesion.add(
        EventoAuditoria(
            actor=responsable.strip(),
            accion="registrar_excepcion",
            entidad="excepcion_hallazgo",
            entidad_id=excepcion.id,
            detalle={
                "legajo": usuario.legajo if usuario else None,
                "categoria": str(hallazgo.categoria),
                "alcance": "regla" if regla_id else "categoria",
                "motivo": motivo,
            },
        )
    )
    sesion.flush()
    return excepcion


def revocar(
    sesion: Session, excepcion: ExcepcionHallazgo, actor: str
) -> ExcepcionHallazgo:
    """Da de baja una excepcion sin borrarla.

    Queda el rastro de que existio y de quien la habia justificado, que es
    lo que permite responderle a una auditoria por que durante seis meses
    ese hallazgo no figuraba.
    """
    if not excepcion.activa:
        raise ErrorExcepcion("la excepcion ya estaba revocada")

    excepcion.activa = False
    excepcion.revocada_en = ahora()
    excepcion.revocada_por = actor
    sesion.add(
        EventoAuditoria(
            actor=actor,
            accion="revocar_excepcion",
            entidad="excepcion_hallazgo",
            entidad_id=excepcion.id,
            detalle={"categoria": excepcion.categoria},
        )
    )
    sesion.flush()
    return excepcion
