"""Modelo de datos de la plataforma (Figura 4.3 del documento).

Trece entidades agrupadas en cuatro bloques: catalogo de accesos
(aplicacion, permiso, rol, rol_permiso), identidades y asignaciones
(usuario, asignacion_acceso), politicas (regla_sod, politica_abac) y
operacion (snapshot, hallazgo, solicitud_alta, script_generado,
evento_auditoria).

Los dominios cerrados de enums.py se replican como restricciones CHECK
para que la base rechace valores invalidos incluso si la escritura no
pasa por la aplicacion.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.nucleo.base_datos import Base
from app.nucleo.enums import (
    Categoria,
    Criticidad,
    EstadoSolicitud,
    EstadoUsuario,
    EstadoValidacion,
    NivelPrivilegio,
    OrigenAsignacion,
    Severidad,
)


def ahora() -> datetime:
    """Marca temporal en UTC. Se centraliza para poder sustituirla en pruebas."""
    return datetime.now(UTC)


def _dominio(columna: str, enumerado: type[StrEnum]) -> CheckConstraint:
    """Genera un CHECK con los valores permitidos de un enumerado."""
    valores = ", ".join(f"'{miembro.value}'" for miembro in enumerado)
    return CheckConstraint(f"{columna} IN ({valores})", name=f"ck_{columna}_dominio")


# ---------------------------------------------------------------------------
# Catalogo de accesos
# ---------------------------------------------------------------------------


class Aplicacion(Base):
    """Sistema destino sobre el que se otorgan accesos."""

    __tablename__ = "aplicacion"
    __table_args__ = (_dominio("criticidad", Criticidad),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    criticidad: Mapped[str] = mapped_column(String(10), nullable=False)
    propietario: Mapped[str] = mapped_column(String(120), nullable=False)
    tecnologia: Mapped[str | None] = mapped_column(String(60))

    permisos: Mapped[list[Permiso]] = relationship(back_populates="aplicacion")
    roles: Mapped[list[Rol]] = relationship(back_populates="aplicacion")


class Permiso(Base):
    """Unidad minima de acceso dentro de una aplicacion."""

    __tablename__ = "permiso"
    __table_args__ = (
        UniqueConstraint("aplicacion_id", "codigo", name="uq_permiso_app_codigo"),
        _dominio("nivel", NivelPrivilegio),
        CheckConstraint("peso_riesgo BETWEEN 1 AND 10", name="ck_permiso_peso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aplicacion_id: Mapped[int] = mapped_column(
        ForeignKey("aplicacion.id", ondelete="CASCADE"), nullable=False, index=True
    )
    codigo: Mapped[str] = mapped_column(String(64), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False)
    nivel: Mapped[str] = mapped_column(String(15), nullable=False)
    peso_riesgo: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    aplicacion: Mapped[Aplicacion] = relationship(back_populates="permisos")


class Rol(Base):
    """Agrupacion de permisos asociada a una funcion de negocio."""

    __tablename__ = "rol"
    __table_args__ = (
        UniqueConstraint("aplicacion_id", "codigo", name="uq_rol_app_codigo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aplicacion_id: Mapped[int] = mapped_column(
        ForeignKey("aplicacion.id", ondelete="CASCADE"), nullable=False, index=True
    )
    codigo: Mapped[str] = mapped_column(String(64), nullable=False)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    puesto_objetivo: Mapped[str | None] = mapped_column(String(120))
    area_objetivo: Mapped[str | None] = mapped_column(String(120))

    aplicacion: Mapped[Aplicacion] = relationship(back_populates="roles")
    permisos: Mapped[list[RolPermiso]] = relationship(back_populates="rol")


class RolPermiso(Base):
    """Tabla intermedia rol-permiso."""

    __tablename__ = "rol_permiso"
    __table_args__ = (
        UniqueConstraint("rol_id", "permiso_id", name="uq_rol_permiso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rol_id: Mapped[int] = mapped_column(
        ForeignKey("rol.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permiso_id: Mapped[int] = mapped_column(
        ForeignKey("permiso.id", ondelete="CASCADE"), nullable=False, index=True
    )

    rol: Mapped[Rol] = relationship(back_populates="permisos")
    permiso: Mapped[Permiso] = relationship()


# ---------------------------------------------------------------------------
# Identidades y asignaciones
# ---------------------------------------------------------------------------


class Usuario(Base):
    """Identidad con los atributos que consume el motor ABAC."""

    __tablename__ = "usuario"
    __table_args__ = (_dominio("estado", EstadoUsuario),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legajo: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    correo: Mapped[str] = mapped_column(String(160), nullable=False)
    area: Mapped[str] = mapped_column(String(120), nullable=False)
    puesto: Mapped[str] = mapped_column(String(120), nullable=False)
    ubicacion: Mapped[str | None] = mapped_column(String(80))
    jefe_legajo: Mapped[str | None] = mapped_column(String(32))
    estado: Mapped[str] = mapped_column(String(10), nullable=False)
    fecha_ingreso: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_baja: Mapped[date | None] = mapped_column(Date)
    ultimo_acceso: Mapped[date | None] = mapped_column(Date)

    asignaciones: Mapped[list[AsignacionAcceso]] = relationship(
        back_populates="usuario"
    )


class AsignacionAcceso(Base):
    """Permiso efectivo que tiene un usuario, con su trazabilidad de origen."""

    __tablename__ = "asignacion_acceso"
    __table_args__ = (
        UniqueConstraint(
            "usuario_id", "permiso_id", name="uq_asignacion_usuario_permiso"
        ),
        _dominio("origen", OrigenAsignacion),
        CheckConstraint(
            "(origen <> 'rbac_rol') OR (rol_id IS NOT NULL)",
            name="ck_asignacion_origen_rol",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permiso_id: Mapped[int] = mapped_column(
        ForeignKey("permiso.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rol_id: Mapped[int | None] = mapped_column(ForeignKey("rol.id", ondelete="SET NULL"))
    origen: Mapped[str] = mapped_column(String(15), nullable=False)
    otorgado_por: Mapped[str | None] = mapped_column(String(120))
    fecha_otorgamiento: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_revision: Mapped[date | None] = mapped_column(Date)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    usuario: Mapped[Usuario] = relationship(back_populates="asignaciones")
    permiso: Mapped[Permiso] = relationship()
    rol: Mapped[Rol | None] = relationship()


# ---------------------------------------------------------------------------
# Politicas
# ---------------------------------------------------------------------------


class ReglaSoD(Base):
    """Par de permisos incompatibles entre si."""

    __tablename__ = "regla_sod"
    __table_args__ = (
        UniqueConstraint("permiso_a_id", "permiso_b_id", name="uq_sod_par"),
        CheckConstraint("permiso_a_id <> permiso_b_id", name="ck_sod_distintos"),
        _dominio("severidad", Severidad),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    descripcion: Mapped[str] = mapped_column(String(300), nullable=False)
    permiso_a_id: Mapped[int] = mapped_column(
        ForeignKey("permiso.id", ondelete="CASCADE"), nullable=False
    )
    permiso_b_id: Mapped[int] = mapped_column(
        ForeignKey("permiso.id", ondelete="CASCADE"), nullable=False
    )
    severidad: Mapped[str] = mapped_column(String(10), nullable=False)
    fundamento: Mapped[str | None] = mapped_column(Text)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    permiso_a: Mapped[Permiso] = relationship(foreign_keys=[permiso_a_id])
    permiso_b: Mapped[Permiso] = relationship(foreign_keys=[permiso_b_id])


class PoliticaABAC(Base):
    """Condicion sobre atributos del usuario que habilita o niega un permiso."""

    __tablename__ = "politica_abac"
    __table_args__ = (
        CheckConstraint("efecto IN ('permitir', 'negar')", name="ck_abac_efecto"),
        CheckConstraint("prioridad BETWEEN 1 AND 100", name="ck_abac_prioridad"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    descripcion: Mapped[str] = mapped_column(String(300), nullable=False)
    permiso_id: Mapped[int] = mapped_column(
        ForeignKey("permiso.id", ondelete="CASCADE"), nullable=False, index=True
    )
    condicion: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    efecto: Mapped[str] = mapped_column(String(10), nullable=False, default="permitir")
    prioridad: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    permiso: Mapped[Permiso] = relationship()


# ---------------------------------------------------------------------------
# Operacion
# ---------------------------------------------------------------------------


class Snapshot(Base):
    """Carga de datos analizada en un momento determinado."""

    __tablename__ = "snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    etiqueta: Mapped[str] = mapped_column(String(80), nullable=False)
    origen_archivo: Mapped[str | None] = mapped_column(String(255))
    hash_archivo: Mapped[str | None] = mapped_column(String(64))
    filas_leidas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filas_descartadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=ahora
    )

    hallazgos: Mapped[list[Hallazgo]] = relationship(back_populates="snapshot")


class Hallazgo(Base):
    """Desviacion detectada por el modulo de analisis."""

    __tablename__ = "hallazgo"
    __table_args__ = (
        _dominio("categoria", Categoria),
        _dominio("severidad", Severidad),
        CheckConstraint("puntaje >= 0", name="ck_hallazgo_puntaje"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshot.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id", ondelete="SET NULL"), index=True
    )
    permiso_id: Mapped[int | None] = mapped_column(
        ForeignKey("permiso.id", ondelete="SET NULL")
    )
    regla_sod_id: Mapped[int | None] = mapped_column(
        ForeignKey("regla_sod.id", ondelete="SET NULL")
    )
    categoria: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    severidad: Mapped[str] = mapped_column(String(10), nullable=False)
    puntaje: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False, default=0)
    evidencia: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    recomendacion: Mapped[str | None] = mapped_column(Text)
    detectado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=ahora
    )

    snapshot: Mapped[Snapshot] = relationship(back_populates="hallazgos")
    usuario: Mapped[Usuario | None] = relationship()
    permiso: Mapped[Permiso | None] = relationship()
    regla_sod: Mapped[ReglaSoD | None] = relationship()
    scripts: Mapped[list[ScriptGenerado]] = relationship(back_populates="hallazgo")


class SolicitudAlta(Base):
    """Pedido de acceso para un usuario nuevo o un cambio de puesto."""

    __tablename__ = "solicitud_alta"
    __table_args__ = (_dominio("estado", EstadoSolicitud),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), nullable=False, index=True
    )
    puesto_solicitado: Mapped[str] = mapped_column(String(120), nullable=False)
    area_solicitada: Mapped[str] = mapped_column(String(120), nullable=False)
    permisos_propuestos: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    conflictos_detectados: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    estado: Mapped[str] = mapped_column(String(10), nullable=False, default="pendiente")
    solicitada_por: Mapped[str | None] = mapped_column(String(120))
    resuelta_por: Mapped[str | None] = mapped_column(String(120))
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=ahora
    )
    resuelta_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    usuario: Mapped[Usuario] = relationship()
    scripts: Mapped[list[ScriptGenerado]] = relationship(back_populates="solicitud")


class ScriptGenerado(Base):
    """Script producido por la capa de IA, con el resultado de los controles."""

    __tablename__ = "script_generado"
    __table_args__ = (
        _dominio("estado_validacion", EstadoValidacion),
        CheckConstraint(
            "(hallazgo_id IS NOT NULL) OR (solicitud_id IS NOT NULL)",
            name="ck_script_tiene_origen",
        ),
        CheckConstraint(
            "(aprobado_por IS NULL) = (aprobado_en IS NULL)",
            name="ck_script_aprobacion_completa",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hallazgo_id: Mapped[int | None] = mapped_column(
        ForeignKey("hallazgo.id", ondelete="CASCADE"), index=True
    )
    solicitud_id: Mapped[int | None] = mapped_column(
        ForeignKey("solicitud_alta.id", ondelete="CASCADE"), index=True
    )
    proveedor: Mapped[str] = mapped_column(String(30), nullable=False)
    modelo: Mapped[str] = mapped_column(String(60), nullable=False)
    lenguaje: Mapped[str] = mapped_column(String(30), nullable=False)
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    estado_validacion: Mapped[str] = mapped_column(
        String(10), nullable=False, default="pendiente"
    )
    controles: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aprobado_por: Mapped[str | None] = mapped_column(String(120))
    aprobado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=ahora
    )

    hallazgo: Mapped[Hallazgo | None] = relationship(back_populates="scripts")
    solicitud: Mapped[SolicitudAlta | None] = relationship(back_populates="scripts")


class EventoAuditoria(Base):
    """Registro inmutable de toda accion con efecto sobre accesos o scripts."""

    __tablename__ = "evento_auditoria"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    accion: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entidad: Mapped[str] = mapped_column(String(60), nullable=False)
    entidad_id: Mapped[int | None] = mapped_column(Integer)
    detalle: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ocurrido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=ahora, index=True
    )
