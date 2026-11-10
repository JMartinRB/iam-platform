"""Esquemas de entrada y salida de la API.

Separados de los modelos de base a proposito. El modelo de base responde a
como se guardan las cosas; estos responden a que necesita la pantalla. Que
hoy se parezcan no quiere decir que tengan que moverse juntos: el dia que
la tabla cambie, la pantalla no tiene por que enterarse.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Snapshots y carga
# ---------------------------------------------------------------------------


class SnapshotResumido(BaseModel):
    id: int
    etiqueta: str
    origen_archivo: str | None = None
    filas_leidas: int
    filas_descartadas: int
    creado_en: datetime
    analizado: bool = False
    usuarios: int = 0


class RechazoSalida(BaseModel):
    archivo: str
    linea: int
    motivo: str
    valor: str = ""


class ResultadoCarga(BaseModel):
    snapshot_id: int
    etiqueta: str
    leidas: int
    aceptadas: int
    rechazadas: int
    rechazos: list[RechazoSalida]
    conteos: dict[str, int]
    segundos: float


class PedidoGeneracionDataset(BaseModel):
    """Genera un conjunto sintetico y lo carga, en un solo paso."""

    perfil: str = Field(
        default="D-1",
        description="Perfil del conjunto: D-1 chico, D-2 mediano, D-3 grande.",
    )
    etiqueta: str | None = None


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------


class CategoriaResumen(BaseModel):
    categoria: str
    titulo: str
    descripcion: str
    severidad_maxima: str
    # Reparto por severidad dentro de la categoría. La severidad de un
    # hallazgo sale del peso del permiso involucrado, así que una misma
    # categoría tiene casos de gravedad distinta: informar sólo el máximo
    # pintaría de crítico un grupo donde lo crítico es un caso de treinta.
    por_severidad: dict[str, int] = Field(default_factory=dict)
    hallazgos: int
    usuarios: int


class ExcepcionSalida(BaseModel):
    """Una excepción justificada sobre un hallazgo (RF-11)."""

    id: int
    motivo: str
    responsable: str
    alcance: str  # regla | categoria
    creada_en: datetime
    legajo: str | None = None
    categoria: str | None = None
    regla: str | None = None


class PedidoExcepcion(BaseModel):
    motivo: str = Field(min_length=3)
    responsable: str = Field(min_length=1)
    # Por omisión la excepción cubre sólo la regla que originó el hallazgo,
    # cuando la categoría la identifica. La alternativa, cubrir toda la
    # categoría para ese usuario, es más amplia y se pide explícitamente.
    solo_esta_regla: bool = True


class ResumenDiagnostico(BaseModel):
    snapshot: SnapshotResumido
    indice_riesgo: int
    nivel_riesgo: str
    hallazgos: int
    criticos: int
    cuentas_dormidas: int
    usuarios_analizados: int
    usuarios_senalados: int
    por_severidad: dict[str, int]
    categorias: list[CategoriaResumen]
    # Hallazgos que el motor emitió y que una excepción justificada deja
    # fuera del panel. Se informa el número para que la exclusión no sea
    # invisible: un panel que esconde cosas sin decir cuántas es peor que
    # uno que las muestra.
    exceptuados: int = 0
    analizado: bool


class HallazgoSalida(BaseModel):
    id: int
    categoria: str
    titulo: str
    severidad: str
    puntaje: float
    legajo: str | None = None
    nombre: str | None = None
    area: str | None = None
    puesto: str | None = None
    permiso: str | None = None
    regla: str | None = None
    recomendacion: str | None = None
    evidencia: dict[str, Any] = Field(default_factory=dict)
    detectado_en: datetime | None = None
    exceptuado: bool = False
    excepcion: ExcepcionSalida | None = None


class DetalleHallazgo(BaseModel):
    """Los cinco elementos que exige RF-10, cada uno con su nombre."""

    hallazgo: HallazgoSalida
    regla_aplicada: str
    umbral: str
    fuente: str
    fecha_snapshot: datetime
    contexto: dict[str, Any]


# ---------------------------------------------------------------------------
# Altas
# ---------------------------------------------------------------------------


class UsuarioSalida(BaseModel):
    id: int
    legajo: str
    nombre: str
    correo: str | None = None
    area: str
    puesto: str
    estado: str
    fecha_ingreso: date | None = None


class PedidoAlta(BaseModel):
    legajo: str
    puesto: str | None = None
    area: str | None = None
    permisos_solicitados: list[str] = Field(default_factory=list)
    conservar_vigentes: bool = True
    solicitada_por: str


class PermisoResueltoSalida(BaseModel):
    codigo: str
    descripcion: str
    origen: str
    rol: str | None = None
    politica: str | None = None
    conflicto: bool = False


class ExclusionSalida(BaseModel):
    codigo: str
    motivo: str
    detalle: str
    regla: str | None = None
    severidad: str | None = None
    permiso_en_conflicto: str | None = None


class SolicitudResumida(BaseModel):
    id: int
    legajo: str
    nombre: str
    puesto_solicitado: str
    area_solicitada: str
    estado: str
    solicitada_por: str
    creada_en: datetime
    otorgados: int
    conflictos: int
    tiene_script: bool = False


class SolicitudDetalle(BaseModel):
    id: int
    usuario: UsuarioSalida
    puesto_solicitado: str
    area_solicitada: str
    estado: str
    solicitada_por: str
    resuelta_por: str | None = None
    creada_en: datetime
    resuelta_en: datetime | None = None
    roles: list[str]
    perfil_inferido: bool
    otorgados: list[PermisoResueltoSalida]
    excluidos: list[ExclusionSalida]
    # Accesos que la persona ya tenía y que la resolución mantiene. No
    # generan ningún cambio sobre la plataforma.
    conservados: list[str] = Field(default_factory=list)
    # Cuántos de los accesos resueltos hay que aplicar efectivamente. Es
    # el delta contra lo conservado, y puede ser cero: si alguien ya tiene
    # todo lo que le corresponde, la decisión es correcta y no hay nada
    # que ejecutar.
    cambios_pendientes: int = 0
    script: ScriptSalida | None = None


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------


class ControlSalida(BaseModel):
    nombre: str
    aprobado: bool
    detalle: str = ""
    hallazgos: list[str] = Field(default_factory=list)


class ScriptSalida(BaseModel):
    id: int
    solicitud_id: int | None = None
    lenguaje: str
    plataforma: str
    contenido: str
    proveedor: str
    modelo: str
    proveedor_es_simulado: bool
    estado_validacion: str
    controles: list[ControlSalida]
    intentos: int
    aprobado_por: str | None = None
    aprobado_en: datetime | None = None
    generado_en: datetime


class PedidoGeneracionScript(BaseModel):
    plataforma: str = Field(default="powershell", pattern="^(powershell|okta)$")
    actor: str
    usar_modelo_real: bool = False


class PedidoAprobacion(BaseModel):
    actor: str
    aprobar: bool = True


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


class EventoSalida(BaseModel):
    id: int
    actor: str
    accion: str
    entidad: str
    entidad_id: int | None = None
    detalle: dict[str, Any] = Field(default_factory=dict)
    ocurrido_en: datetime


SolicitudDetalle.model_rebuild()
