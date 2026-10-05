"""Importacion de un export de accesos a la base.

La carga es la primera cosa que ve el sistema, y es donde los datos reales
son mas desprolijos. El criterio es no abortar: cada fila se valida por su
cuenta, las validas entran y las que no quedan registradas con el motivo del
rechazo, para que el analista sepa exactamente que se perdio y por que. Es
el comportamiento que exige el caso de aceptacion CP-01.

Se lee con el modulo csv de la biblioteca estandar y no con pandas, a
proposito: hace falta saber el numero de linea de cada fila rechazada, y eso
en un dataframe se vuelve indirecto. pandas se usa mas adelante, en el
modulo de analisis, donde el trabajo si es de agregacion.
"""
from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.nucleo.enums import (
    Criticidad,
    EstadoUsuario,
    NivelPrivilegio,
    OrigenAsignacion,
    Severidad,
)
from app.nucleo.modelos import (
    Aplicacion,
    AsignacionAcceso,
    EventoAuditoria,
    Permiso,
    ReglaSoD,
    Rol,
    RolPermiso,
    Snapshot,
    Usuario,
)
from app.nucleo.texto import (
    normalizar_codigo,
    normalizar_identificador,
    normalizar_texto,
)

ARCHIVOS = (
    "aplicaciones.csv",
    "permisos.csv",
    "roles.csv",
    "roles_permisos.csv",
    "usuarios.csv",
    "asignaciones.csv",
    "reglas_sod.csv",
)

VERDADEROS = {"true", "1", "si", "sí", "yes", "x"}


@dataclass
class Rechazo:
    """Una fila que no entro, con el dato suficiente para entenderlo."""

    archivo: str
    linea: int
    motivo: str
    valor: str = ""

    def __str__(self) -> str:
        pie = f" ({self.valor})" if self.valor else ""
        return f"{self.archivo}:{self.linea} — {self.motivo}{pie}"


@dataclass
class ResultadoImportacion:
    snapshot_id: int
    etiqueta: str
    leidas: int = 0
    aceptadas: int = 0
    rechazos: list[Rechazo] = field(default_factory=list)
    conteos: dict[str, int] = field(default_factory=dict)

    @property
    def descartadas(self) -> int:
        return len(self.rechazos)

    def motivos(self) -> dict[str, int]:
        """Rechazos agrupados por motivo, que es como se muestran en pantalla."""
        resumen: dict[str, int] = {}
        for r in self.rechazos:
            resumen[r.motivo] = resumen.get(r.motivo, 0) + 1
        return resumen


class ErrorImportacion(Exception):
    """El dataset no se puede leer: falta un archivo o una columna."""


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------


def _filas(ruta: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """Devuelve cada fila con su numero de linea real en el archivo."""
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        if lector.fieldnames is None:
            raise ErrorImportacion(f"{ruta.name} esta vacio")
        for numero, fila in enumerate(lector, start=2):
            yield numero, {k: (v or "").strip() for k, v in fila.items() if k}


def _fecha(valor: str) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _booleano(valor: str) -> bool:
    return valor.strip().lower() in VERDADEROS


def _hash_carpeta(carpeta: Path) -> str:
    """Huella del conjunto completo, para poder repetir un analisis."""
    resumen = hashlib.sha256()
    for nombre in ARCHIVOS:
        ruta = carpeta / nombre
        if ruta.exists():
            resumen.update(ruta.read_bytes())
    return resumen.hexdigest()


# ---------------------------------------------------------------------------
# Importacion
# ---------------------------------------------------------------------------


def importar_dataset(
    sesion: Session,
    carpeta: Path,
    etiqueta: str | None = None,
    actor: str = "sistema",
) -> ResultadoImportacion:
    """Carga un dataset completo y devuelve el detalle de lo que entro.

    El orden de carga sigue las dependencias: primero el catalogo, despues
    las identidades, por ultimo las asignaciones y las reglas, que apuntan a
    todo lo anterior.
    """
    carpeta = Path(carpeta)
    faltantes = [n for n in ARCHIVOS if not (carpeta / n).exists()]
    if faltantes:
        raise ErrorImportacion(
            f"faltan archivos en {carpeta.name}: {', '.join(faltantes)}"
        )

    snapshot = Snapshot(
        etiqueta=etiqueta or carpeta.name,
        origen_archivo=str(carpeta),
        hash_archivo=_hash_carpeta(carpeta),
    )
    sesion.add(snapshot)
    sesion.flush()

    resultado = ResultadoImportacion(
        snapshot_id=snapshot.id, etiqueta=snapshot.etiqueta
    )

    apps = _cargar_aplicaciones(sesion, carpeta, resultado)
    permisos = _cargar_permisos(sesion, carpeta, apps, resultado)
    roles = _cargar_roles(sesion, carpeta, apps, resultado)
    _cargar_rol_permiso(sesion, carpeta, roles, permisos, resultado)
    usuarios = _cargar_usuarios(sesion, carpeta, resultado)
    _cargar_asignaciones(sesion, carpeta, usuarios, permisos, roles, resultado)
    _cargar_reglas_sod(sesion, carpeta, permisos, resultado)

    snapshot.filas_leidas = resultado.leidas
    snapshot.filas_descartadas = resultado.descartadas
    sesion.add(
        EventoAuditoria(
            actor=actor,
            accion="importar_dataset",
            entidad="snapshot",
            entidad_id=snapshot.id,
            detalle={
                "etiqueta": snapshot.etiqueta,
                "leidas": resultado.leidas,
                "aceptadas": resultado.aceptadas,
                "descartadas": resultado.descartadas,
                "motivos": resultado.motivos(),
            },
        )
    )
    sesion.flush()
    return resultado


def _cargar_aplicaciones(
    sesion: Session, carpeta: Path, r: ResultadoImportacion
) -> dict[str, Aplicacion]:
    apps: dict[str, Aplicacion] = {}
    criticidades = {c.value for c in Criticidad}
    for linea, fila in _filas(carpeta / "aplicaciones.csv"):
        r.leidas += 1
        codigo = normalizar_codigo(fila.get("codigo"))
        if not codigo:
            r.rechazos.append(Rechazo("aplicaciones.csv", linea, "codigo vacio"))
            continue
        if codigo in apps:
            r.rechazos.append(
                Rechazo("aplicaciones.csv", linea, "aplicacion repetida", codigo)
            )
            continue
        criticidad = fila.get("criticidad", "").lower()
        if criticidad not in criticidades:
            r.rechazos.append(
                Rechazo(
                    "aplicaciones.csv", linea, "criticidad fuera del dominio", criticidad
                )
            )
            continue
        app = Aplicacion(
            codigo=codigo,
            nombre=normalizar_texto(fila.get("nombre")) or codigo,
            criticidad=criticidad,
            propietario=normalizar_texto(fila.get("propietario")) or "sin asignar",
            tecnologia=normalizar_texto(fila.get("tecnologia")) or None,
        )
        sesion.add(app)
        apps[codigo] = app
        r.aceptadas += 1
    sesion.flush()
    r.conteos["aplicaciones"] = len(apps)
    return apps


def _cargar_permisos(
    sesion: Session,
    carpeta: Path,
    apps: dict[str, Aplicacion],
    r: ResultadoImportacion,
) -> dict[str, Permiso]:
    permisos: dict[str, Permiso] = {}
    niveles = {n.value for n in NivelPrivilegio}
    for linea, fila in _filas(carpeta / "permisos.csv"):
        r.leidas += 1
        codigo = normalizar_codigo(fila.get("codigo"))
        app_codigo = normalizar_codigo(fila.get("aplicacion_codigo"))
        if not codigo:
            r.rechazos.append(Rechazo("permisos.csv", linea, "codigo vacio"))
            continue
        if codigo in permisos:
            r.rechazos.append(
                Rechazo("permisos.csv", linea, "permiso repetido", codigo)
            )
            continue
        if app_codigo not in apps:
            r.rechazos.append(
                Rechazo("permisos.csv", linea, "aplicacion inexistente", app_codigo)
            )
            continue
        nivel = fila.get("nivel", "").lower()
        if nivel not in niveles:
            r.rechazos.append(
                Rechazo("permisos.csv", linea, "nivel fuera del dominio", nivel)
            )
            continue
        try:
            peso = int(fila.get("peso_riesgo") or 1)
        except ValueError:
            r.rechazos.append(
                Rechazo(
                    "permisos.csv", linea, "peso de riesgo no numerico",
                    fila.get("peso_riesgo", ""),
                )
            )
            continue
        if not 1 <= peso <= 10:
            r.rechazos.append(
                Rechazo("permisos.csv", linea, "peso de riesgo fuera de rango", str(peso))
            )
            continue
        permiso = Permiso(
            aplicacion_id=apps[app_codigo].id,
            codigo=codigo,
            descripcion=normalizar_texto(fila.get("descripcion")) or codigo,
            nivel=nivel,
            peso_riesgo=peso,
        )
        sesion.add(permiso)
        permisos[codigo] = permiso
        r.aceptadas += 1
    sesion.flush()
    r.conteos["permisos"] = len(permisos)
    return permisos


def _cargar_roles(
    sesion: Session,
    carpeta: Path,
    apps: dict[str, Aplicacion],
    r: ResultadoImportacion,
) -> dict[str, Rol]:
    roles: dict[str, Rol] = {}
    for linea, fila in _filas(carpeta / "roles.csv"):
        r.leidas += 1
        codigo = normalizar_codigo(fila.get("codigo"))
        app_codigo = normalizar_codigo(fila.get("aplicacion_codigo"))
        if not codigo or codigo in roles:
            r.rechazos.append(
                Rechazo("roles.csv", linea, "codigo vacio o repetido", codigo)
            )
            continue
        if app_codigo not in apps:
            r.rechazos.append(
                Rechazo("roles.csv", linea, "aplicacion inexistente", app_codigo)
            )
            continue
        rol = Rol(
            aplicacion_id=apps[app_codigo].id,
            codigo=codigo,
            nombre=normalizar_texto(fila.get("nombre")) or codigo,
            puesto_objetivo=normalizar_texto(fila.get("puesto_objetivo")) or None,
            area_objetivo=normalizar_texto(fila.get("area_objetivo")) or None,
        )
        sesion.add(rol)
        roles[codigo] = rol
        r.aceptadas += 1
    sesion.flush()
    r.conteos["roles"] = len(roles)
    return roles


def _cargar_rol_permiso(
    sesion: Session,
    carpeta: Path,
    roles: dict[str, Rol],
    permisos: dict[str, Permiso],
    r: ResultadoImportacion,
) -> None:
    vistos: set[tuple[str, str]] = set()
    for linea, fila in _filas(carpeta / "roles_permisos.csv"):
        r.leidas += 1
        rol_codigo = normalizar_codigo(fila.get("rol_codigo"))
        permiso_codigo = normalizar_codigo(fila.get("permiso_codigo"))
        if rol_codigo not in roles:
            r.rechazos.append(
                Rechazo("roles_permisos.csv", linea, "rol inexistente", rol_codigo)
            )
            continue
        if permiso_codigo not in permisos:
            r.rechazos.append(
                Rechazo(
                    "roles_permisos.csv", linea, "permiso inexistente", permiso_codigo
                )
            )
            continue
        if (rol_codigo, permiso_codigo) in vistos:
            r.rechazos.append(
                Rechazo(
                    "roles_permisos.csv", linea, "asociacion repetida", permiso_codigo
                )
            )
            continue
        vistos.add((rol_codigo, permiso_codigo))
        sesion.add(
            RolPermiso(
                rol_id=roles[rol_codigo].id, permiso_id=permisos[permiso_codigo].id
            )
        )
        r.aceptadas += 1
    sesion.flush()
    r.conteos["roles_permisos"] = len(vistos)


def _cargar_usuarios(
    sesion: Session, carpeta: Path, r: ResultadoImportacion
) -> dict[str, Usuario]:
    usuarios: dict[str, Usuario] = {}
    estados = {e.value for e in EstadoUsuario}
    for linea, fila in _filas(carpeta / "usuarios.csv"):
        r.leidas += 1
        legajo = fila.get("legajo", "").upper()
        if not legajo:
            r.rechazos.append(Rechazo("usuarios.csv", linea, "legajo vacio"))
            continue
        if legajo in usuarios:
            # El export trae la misma persona dos veces, cosa habitual cuando
            # se consolidan fuentes. Se conserva la primera aparicion.
            r.rechazos.append(
                Rechazo("usuarios.csv", linea, "legajo duplicado", legajo)
            )
            continue
        estado = fila.get("estado", "").lower()
        if estado not in estados:
            r.rechazos.append(
                Rechazo("usuarios.csv", linea, "estado fuera del dominio", estado)
            )
            continue
        ingreso = _fecha(fila.get("fecha_ingreso", ""))
        if ingreso is None:
            r.rechazos.append(
                Rechazo(
                    "usuarios.csv", linea, "fecha de ingreso invalida",
                    fila.get("fecha_ingreso", ""),
                )
            )
            continue
        usuario = Usuario(
            legajo=legajo,
            nombre=normalizar_texto(fila.get("nombre")) or legajo,
            correo=normalizar_texto(fila.get("correo")),
            area=normalizar_texto(fila.get("area")) or "sin area",
            puesto=normalizar_texto(fila.get("puesto")) or "sin puesto",
            ubicacion=normalizar_texto(fila.get("ubicacion")) or None,
            jefe_legajo=fila.get("jefe_legajo", "").upper() or None,
            estado=estado,
            fecha_ingreso=ingreso,
            fecha_baja=_fecha(fila.get("fecha_baja", "")),
            ultimo_acceso=_fecha(fila.get("ultimo_acceso", "")),
        )
        sesion.add(usuario)
        usuarios[legajo] = usuario
        r.aceptadas += 1
    sesion.flush()
    r.conteos["usuarios"] = len(usuarios)
    return usuarios


def _cargar_asignaciones(
    sesion: Session,
    carpeta: Path,
    usuarios: dict[str, Usuario],
    permisos: dict[str, Permiso],
    roles: dict[str, Rol],
    r: ResultadoImportacion,
) -> None:
    origenes = {o.value for o in OrigenAsignacion}
    vistos: set[tuple[str, str]] = set()
    for linea, fila in _filas(carpeta / "asignaciones.csv"):
        r.leidas += 1
        legajo = fila.get("legajo", "").upper()
        permiso_codigo = normalizar_codigo(fila.get("permiso_codigo"))
        origen = fila.get("origen", "").lower()
        rol_codigo = normalizar_codigo(fila.get("rol_codigo"))

        if not legajo:
            r.rechazos.append(Rechazo("asignaciones.csv", linea, "legajo vacio"))
            continue
        if legajo not in usuarios:
            r.rechazos.append(
                Rechazo("asignaciones.csv", linea, "usuario inexistente", legajo)
            )
            continue
        if permiso_codigo not in permisos:
            r.rechazos.append(
                Rechazo(
                    "asignaciones.csv", linea, "permiso inexistente", permiso_codigo
                )
            )
            continue
        if origen not in origenes:
            r.rechazos.append(
                Rechazo("asignaciones.csv", linea, "origen fuera del dominio", origen)
            )
            continue
        otorgamiento = _fecha(fila.get("fecha_otorgamiento", ""))
        if otorgamiento is None:
            r.rechazos.append(
                Rechazo(
                    "asignaciones.csv", linea, "fecha de otorgamiento invalida",
                    fila.get("fecha_otorgamiento", ""),
                )
            )
            continue
        if origen == OrigenAsignacion.RBAC_ROL and rol_codigo not in roles:
            r.rechazos.append(
                Rechazo(
                    "asignaciones.csv", linea,
                    "asignacion por rol sin rol valido", rol_codigo,
                )
            )
            continue
        if (legajo, permiso_codigo) in vistos:
            r.rechazos.append(
                Rechazo(
                    "asignaciones.csv", linea, "asignacion repetida", permiso_codigo
                )
            )
            continue

        vistos.add((legajo, permiso_codigo))
        sesion.add(
            AsignacionAcceso(
                usuario_id=usuarios[legajo].id,
                permiso_id=permisos[permiso_codigo].id,
                rol_id=roles[rol_codigo].id if rol_codigo in roles else None,
                origen=origen,
                otorgado_por=fila.get("otorgado_por") or None,
                fecha_otorgamiento=otorgamiento,
                fecha_revision=_fecha(fila.get("fecha_revision", "")),
                activa=_booleano(fila.get("activa", "true")),
            )
        )
        r.aceptadas += 1
    sesion.flush()
    r.conteos["asignaciones"] = len(vistos)


def _cargar_reglas_sod(
    sesion: Session,
    carpeta: Path,
    permisos: dict[str, Permiso],
    r: ResultadoImportacion,
) -> None:
    severidades = {s.value for s in Severidad}
    vistos: set[str] = set()
    for linea, fila in _filas(carpeta / "reglas_sod.csv"):
        r.leidas += 1
        codigo = normalizar_identificador(fila.get("codigo"))
        a = normalizar_codigo(fila.get("permiso_a"))
        b = normalizar_codigo(fila.get("permiso_b"))
        severidad = fila.get("severidad", "").lower()

        if not codigo or codigo in vistos:
            r.rechazos.append(
                Rechazo("reglas_sod.csv", linea, "codigo vacio o repetido", codigo)
            )
            continue
        if a not in permisos or b not in permisos:
            r.rechazos.append(
                Rechazo("reglas_sod.csv", linea, "permiso inexistente", f"{a} / {b}")
            )
            continue
        if a == b:
            r.rechazos.append(
                Rechazo(
                    "reglas_sod.csv", linea,
                    "la regla enfrenta un permiso consigo mismo", a,
                )
            )
            continue
        if severidad not in severidades:
            r.rechazos.append(
                Rechazo("reglas_sod.csv", linea, "severidad fuera del dominio", severidad)
            )
            continue

        vistos.add(codigo)
        sesion.add(
            ReglaSoD(
                codigo=codigo,
                descripcion=normalizar_texto(fila.get("descripcion")) or codigo,
                permiso_a_id=permisos[a].id,
                permiso_b_id=permisos[b].id,
                severidad=severidad,
                fundamento=normalizar_texto(fila.get("fundamento")) or None,
                activa=_booleano(fila.get("activa", "true")),
            )
        )
        r.aceptadas += 1
    sesion.flush()
    r.conteos["reglas_sod"] = len(vistos)


def resumen(resultado: ResultadoImportacion) -> dict[str, Any]:
    """Forma en que la API devuelve el resultado de una carga."""
    return {
        "snapshot_id": resultado.snapshot_id,
        "etiqueta": resultado.etiqueta,
        "filas_leidas": resultado.leidas,
        "filas_aceptadas": resultado.aceptadas,
        "filas_descartadas": resultado.descartadas,
        "motivos": resultado.motivos(),
        "conteos": resultado.conteos,
        "rechazos": [str(x) for x in resultado.rechazos[:100]],
    }


def catalogo_cargado(sesion: Session) -> bool:
    """Indica si ya hay un catalogo en la base."""
    return sesion.scalar(select(Aplicacion).limit(1)) is not None
