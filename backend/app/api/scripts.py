"""Scripts generados: listado, detalle y aprobación humana."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencias import sesion_db
from app.api.esquemas import ControlSalida, PedidoAprobacion, ScriptSalida
from app.modulos.generacion import aprobar_script
from app.modulos.proveedores import Proveedor, ProveedorSimulado, obtener_proveedor
from app.nucleo.enums import EstadoValidacion
from app.nucleo.modelos import ScriptGenerado, ahora

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

# Nombre de la plataforma destino, para rotular la pantalla. El campo que
# se guarda es el identificador con el que se pidio la generacion.
PLATAFORMA_DE_LENGUAJE = {"powershell": "Active Directory", "okta": "Okta"}


def proveedor_para(usar_modelo_real: bool) -> Proveedor:
    """Elige con qué se genera, y nunca falla por culpa de la red.

    Por defecto se usa el proveedor simulado, que arma la respuesta a
    partir del propio contexto. No es un modelo de lenguaje y el sistema lo
    informa en todas las pantallas: presentar su salida como si viniera de
    un modelo sería faltar a la verdad sobre lo único que este trabajo
    pide creer.
    """
    if not usar_modelo_real:
        return ProveedorSimulado()
    try:
        return obtener_proveedor()
    except Exception:
        return ProveedorSimulado()


def a_salida(s: ScriptGenerado) -> ScriptSalida:
    controles = [
        ControlSalida(
            nombre=c.get("nombre", ""),
            aprobado=bool(c.get("aprobado")),
            detalle=c.get("detalle", ""),
            hallazgos=list(c.get("hallazgos", [])),
        )
        for c in (s.controles or {}).get("detalle", {}).get("controles", [])
    ]
    return ScriptSalida(
        id=s.id,
        solicitud_id=s.solicitud_id,
        lenguaje=s.lenguaje,
        plataforma=PLATAFORMA_DE_LENGUAJE.get(s.lenguaje, s.lenguaje),
        contenido=s.contenido,
        proveedor=s.proveedor,
        modelo=s.modelo,
        proveedor_es_simulado=s.proveedor == ProveedorSimulado.nombre,
        estado_validacion=str(s.estado_validacion),
        controles=controles,
        intentos=s.intentos,
        aprobado_por=s.aprobado_por,
        aprobado_en=s.aprobado_en,
        generado_en=s.generado_en,
    )


@router.get("", response_model=list[ScriptSalida])
def listar(sesion: Session = Depends(sesion_db)) -> list[ScriptSalida]:
    """Scripts generados, del más reciente al más viejo."""
    filas = sesion.scalars(
        select(ScriptGenerado).order_by(ScriptGenerado.id.desc())
    ).all()
    return [a_salida(s) for s in filas]


@router.get("/{script_id}", response_model=ScriptSalida)
def ver(script_id: int, sesion: Session = Depends(sesion_db)) -> ScriptSalida:
    return a_salida(_script(sesion, script_id))


def _script(sesion: Session, script_id: int) -> ScriptGenerado:
    script = sesion.get(ScriptGenerado, script_id)
    if script is None:
        raise HTTPException(status_code=404, detail="no existe ese script")
    return script


@router.post("/{script_id}/aprobar", response_model=ScriptSalida)
def aprobar(
    script_id: int, pedido: PedidoAprobacion, sesion: Session = Depends(sesion_db)
) -> ScriptSalida:
    """Aprueba o rechaza un script.

    Un script que no pasó la cadena de validación no se puede aprobar por
    pantalla. Si la máquina ya dijo que la salida no sirve, dejar que una
    persona la habilite con un clic volvería decorativos los seis
    controles.
    """
    script = _script(sesion, script_id)
    if script.aprobado_por is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"el script ya fue resuelto por {script.aprobado_por}",
        )

    if not pedido.aprobar:
        script.estado_validacion = EstadoValidacion.RECHAZADO
        script.aprobado_por = pedido.actor
        script.aprobado_en = ahora()
        sesion.flush()
        return a_salida(script)

    if script.estado_validacion != EstadoValidacion.VALIDO:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "no se puede aprobar un script que no pasó la cadena de "
                f"validación (estado: {script.estado_validacion})"
            ),
        )

    aprobar_script(sesion, script, aprobado_por=pedido.actor)
    sesion.flush()
    return a_salida(script)
