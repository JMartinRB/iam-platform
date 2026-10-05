"""Dominios cerrados del modelo. Se validan con restricciones CHECK en la base."""
from enum import StrEnum


class Criticidad(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class NivelPrivilegio(StrEnum):
    LECTURA = "lectura"
    ESCRITURA = "escritura"
    ADMINISTRACION = "administracion"


class EstadoUsuario(StrEnum):
    ACTIVO = "activo"
    INACTIVO = "inactivo"
    BAJA = "baja"


class OrigenAsignacion(StrEnum):
    RBAC_ROL = "rbac_rol"
    ABAC_ATRIBUTO = "abac_atributo"
    DIRECTA = "directa"


class Categoria(StrEnum):
    CUENTA_DORMIDA = "cuenta_dormida"
    CUENTA_HUERFANA = "cuenta_huerfana"
    SOBRE_PRIVILEGIO = "sobre_privilegio"
    CONFLICTO_SOD = "conflicto_sod"
    PRIVILEGE_CREEP = "privilege_creep"


class Severidad(StrEnum):
    CRITICA = "critica"
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class EstadoSolicitud(StrEnum):
    PENDIENTE = "pendiente"
    APROBADA = "aprobada"
    RECHAZADA = "rechazada"


class EstadoValidacion(StrEnum):
    PENDIENTE = "pendiente"
    VALIDO = "valido"
    RECHAZADO = "rechazado"


PESO_SEVERIDAD = {
    Severidad.CRITICA: 10,
    Severidad.ALTA: 6,
    Severidad.MEDIA: 3,
    Severidad.BAJA: 1,
}
