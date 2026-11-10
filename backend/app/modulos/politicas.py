"""Motor de politicas: resuelve que accesos le corresponden a una persona.

El flujo de alta funciona en tres pasos, y el orden importa:

1. RBAC propone. Los roles del puesto aportan el conjunto base de permisos.
   Cuando el catalogo declara a que puesto apunta cada rol, se usa esa
   declaracion; cuando no, el perfil se infiere de lo que efectivamente
   tienen los pares del mismo puesto. La segunda via no es un atajo: en una
   organizacion real el catalogo de roles casi nunca esta completo, y un
   motor que solo funcione con el catalogo declarado no se puede usar.

2. ABAC ajusta. Las politicas de atributos pueden sumar un permiso cuando
   la condicion se cumple, y pueden negarlo. La negacion gana siempre,
   venga el permiso del rol o de otra politica: es la unica regla de
   precedencia que no deja huecos.

3. La validacion cruzada de Segregacion de Funciones revisa el conjunto
   resultante contra la matriz de incompatibilidades, y no solo dentro de
   la propuesta: tambien contra lo que la persona ya tiene. Un permiso
   inofensivo en abstracto puede ser incompatible con algo que la persona
   arrastra de su puesto anterior, y ese es justamente el caso que un alta
   por formulario deja pasar.

Lo que el motor devuelve es una decision cerrada, con el detalle de por que
cada permiso quedo adentro o afuera. Recien sobre esa decision trabaja la
capa de generacion de scripts.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modulos.expresiones import describir, evaluar
from app.nucleo.enums import EstadoSolicitud, EstadoUsuario, Severidad
from app.nucleo.modelos import (
    AsignacionAcceso,
    EventoAuditoria,
    Permiso,
    PoliticaABAC,
    ReglaSoD,
    Rol,
    RolPermiso,
    SolicitudAlta,
    Usuario,
)

#: Proporcion de los pares de un puesto que tiene que tener un rol para que
#: se lo considere parte del perfil de ese puesto.
UMBRAL_PERFIL = 0.6

#: Cantidad minima de pares para que inferir el perfil tenga sentido.
MINIMO_PARES = 3


@dataclass
class PermisoResuelto:
    codigo: str
    descripcion: str
    origen: str  # rbac_rol | abac_atributo
    rol: str | None = None
    politica: str | None = None


@dataclass
class Exclusion:
    codigo: str
    motivo: str
    detalle: str
    regla: str | None = None
    severidad: str | None = None
    permiso_en_conflicto: str | None = None


@dataclass
class Resolucion:
    """Decision completa sobre un alta, lista para ser aprobada o rechazada."""

    legajo: str
    puesto: str
    area: str
    otorgados: list[PermisoResuelto] = field(default_factory=list)
    excluidos: list[Exclusion] = field(default_factory=list)
    conservados: list[str] = field(default_factory=list)
    perfil_inferido: bool = False
    roles: list[str] = field(default_factory=list)

    @property
    def tiene_conflictos(self) -> bool:
        return any(e.motivo == "conflicto_sod" for e in self.excluidos)

    def como_diccionario(self) -> dict[str, Any]:
        return {
            "legajo": self.legajo,
            "puesto": self.puesto,
            "area": self.area,
            "roles": self.roles,
            "perfil_inferido": self.perfil_inferido,
            "otorgados": [
                {
                    "permiso": p.codigo,
                    "descripcion": p.descripcion,
                    "origen": p.origen,
                    "rol": p.rol,
                    "politica": p.politica,
                }
                for p in self.otorgados
            ],
            "excluidos": [
                {
                    "permiso": e.codigo,
                    "motivo": e.motivo,
                    "detalle": e.detalle,
                    "regla": e.regla,
                    "severidad": e.severidad,
                    "permiso_en_conflicto": e.permiso_en_conflicto,
                }
                for e in self.excluidos
            ],
            "conservados": self.conservados,
        }

    @classmethod
    def desde_diccionario(cls, datos: dict[str, Any]) -> Resolucion:
        """Reconstruye la resolucion que quedo guardada en la solicitud.

        El inverso exacto de ``como_diccionario``. Hace falta porque el
        script se genera despues de que la solicitud se guardo, y tiene que
        generarse contra la decision que efectivamente se valido y no
        contra una recalculada: entre un momento y el otro el catalogo pudo
        cambiar, y si se recalculara, el control de correspondencia estaria
        comparando el script contra una decision distinta de la que
        aprueba la persona.
        """
        return cls(
            legajo=datos.get("legajo", ""),
            puesto=datos.get("puesto", ""),
            area=datos.get("area", ""),
            roles=list(datos.get("roles", [])),
            perfil_inferido=bool(datos.get("perfil_inferido", False)),
            conservados=list(datos.get("conservados", [])),
            otorgados=[
                PermisoResuelto(
                    codigo=p["permiso"],
                    descripcion=p.get("descripcion", ""),
                    origen=p.get("origen", ""),
                    rol=p.get("rol"),
                    politica=p.get("politica"),
                )
                for p in datos.get("otorgados", [])
            ],
            excluidos=[
                Exclusion(
                    codigo=e["permiso"],
                    motivo=e.get("motivo", ""),
                    detalle=e.get("detalle", ""),
                    regla=e.get("regla"),
                    severidad=e.get("severidad"),
                    permiso_en_conflicto=e.get("permiso_en_conflicto"),
                )
                for e in datos.get("excluidos", [])
            ],
        )


@dataclass
class _Catalogo:
    permisos: dict[int, Permiso]
    roles: dict[int, Rol]
    permisos_de_rol: dict[int, set[int]]
    politicas: dict[int, list[PoliticaABAC]]
    reglas: list[ReglaSoD]
    roles_por_puesto: dict[str, set[int]]
    perfiles_declarados: set[str]


def _cargar_catalogo(sesion: Session) -> _Catalogo:
    permisos = {p.id: p for p in sesion.scalars(select(Permiso)).all()}
    roles = {r.id: r for r in sesion.scalars(select(Rol)).all()}

    permisos_de_rol: dict[int, set[int]] = defaultdict(set)
    for rp in sesion.scalars(select(RolPermiso)).all():
        permisos_de_rol[rp.rol_id].add(rp.permiso_id)

    politicas: dict[int, list[PoliticaABAC]] = defaultdict(list)
    for politica in sesion.scalars(
        select(PoliticaABAC).where(PoliticaABAC.activa.is_(True))
    ).all():
        politicas[politica.permiso_id].append(politica)
    for lista in politicas.values():
        lista.sort(key=lambda p: p.prioridad, reverse=True)

    reglas = list(
        sesion.scalars(select(ReglaSoD).where(ReglaSoD.activa.is_(True))).all()
    )

    roles_por_puesto, declarados = _perfiles(sesion, roles, permisos_de_rol)
    return _Catalogo(
        permisos=permisos,
        roles=roles,
        permisos_de_rol=permisos_de_rol,
        politicas=politicas,
        reglas=reglas,
        roles_por_puesto=roles_por_puesto,
        perfiles_declarados=declarados,
    )


def _perfiles(
    sesion: Session, roles: dict[int, Rol], permisos_de_rol: dict[int, set[int]]
) -> tuple[dict[str, set[int]], set[str]]:
    """Arma el perfil de cada puesto: declarado si existe, inferido si no.

    La inferencia es un recuento simple: un rol pertenece al perfil de un
    puesto si lo tiene al menos el 60 % de las personas activas de ese
    puesto. Es deliberadamente conservador. Un umbral mas bajo arrastraria
    al perfil los accesos excepcionales de unos pocos, que es exactamente lo
    que el sistema deberia estar sacando y no propagando a cada alta nueva.
    """
    por_puesto: dict[str, set[int]] = defaultdict(set)
    declarados: set[str] = set()

    for rol in roles.values():
        if rol.puesto_objetivo:
            por_puesto[rol.puesto_objetivo].add(rol.id)
            declarados.add(rol.puesto_objetivo)

    filas = sesion.execute(
        select(Usuario.puesto, AsignacionAcceso.rol_id, Usuario.id)
        .join(AsignacionAcceso, AsignacionAcceso.usuario_id == Usuario.id)
        .where(
            Usuario.estado == EstadoUsuario.ACTIVO,
            AsignacionAcceso.activa.is_(True),
            AsignacionAcceso.rol_id.is_not(None),
        )
    ).all()

    titulares: dict[tuple[str, int], set[int]] = defaultdict(set)
    personas: dict[str, set[int]] = defaultdict(set)
    for puesto, rol_id, usuario_id in filas:
        titulares[(puesto, rol_id)].add(usuario_id)
        personas[puesto].add(usuario_id)

    for (puesto, rol_id), usuarios in titulares.items():
        if puesto in declarados:
            continue
        total = len(personas[puesto])
        if total < MINIMO_PARES:
            continue
        if len(usuarios) / total >= UMBRAL_PERFIL:
            por_puesto[puesto].add(rol_id)

    return por_puesto, declarados


def atributos_de(usuario: Usuario, fecha_corte: date) -> dict[str, Any]:
    """Atributos que ve el interprete de politicas."""
    return {
        "area": usuario.area,
        "puesto": usuario.puesto,
        "ubicacion": usuario.ubicacion,
        "estado": usuario.estado,
        "legajo": usuario.legajo,
        "jefe_legajo": usuario.jefe_legajo,
        "antiguedad_dias": (fecha_corte - usuario.fecha_ingreso).days,
    }


# ---------------------------------------------------------------------------
# Resolucion
# ---------------------------------------------------------------------------


def resolver_alta(
    sesion: Session,
    usuario: Usuario,
    puesto: str | None = None,
    area: str | None = None,
    fecha_corte: date | None = None,
    conservar_vigentes: bool = True,
    permisos_solicitados: list[str] | None = None,
) -> Resolucion:
    """Resuelve los accesos de un alta o de un cambio de puesto.

    ``conservar_vigentes`` distingue los dos casos. En un alta nueva la
    persona no tiene nada y la validacion cruzada solo mira la propuesta. En
    un cambio de puesto sigue teniendo lo anterior, y es ahi donde aparecen
    los conflictos que ningun formulario detecta: el permiso nuevo es
    inofensivo, el viejo tambien, y juntos no.

    ``permisos_solicitados`` son los accesos que el analista pide a mano por
    fuera del rol. Entran a la propuesta como cualquier otro y pasan por las
    mismas validaciones: pedirlo explicitamente no saltea nada.
    """
    fecha_corte = fecha_corte or date.today()
    puesto = puesto or usuario.puesto
    area = area or usuario.area
    catalogo = _cargar_catalogo(sesion)

    resolucion = Resolucion(legajo=usuario.legajo, puesto=puesto, area=area)
    atributos = {**atributos_de(usuario, fecha_corte), "puesto": puesto, "area": area}

    # 1. RBAC
    roles = catalogo.roles_por_puesto.get(puesto, set())
    resolucion.perfil_inferido = puesto not in catalogo.perfiles_declarados
    resolucion.roles = sorted(
        catalogo.roles[r].codigo for r in roles if r in catalogo.roles
    )

    propuesta: dict[int, PermisoResuelto] = {}
    for rol_id in roles:
        rol = catalogo.roles.get(rol_id)
        if rol is None:
            continue
        for permiso_id in catalogo.permisos_de_rol.get(rol_id, set()):
            permiso = catalogo.permisos[permiso_id]
            propuesta.setdefault(
                permiso_id,
                PermisoResuelto(
                    codigo=permiso.codigo,
                    descripcion=permiso.descripcion,
                    origen="rbac_rol",
                    rol=rol.codigo,
                ),
            )

    # 1b. Permisos pedidos a mano, por fuera del rol
    if permisos_solicitados:
        por_codigo = {p.codigo: p for p in catalogo.permisos.values()}
        for codigo in permisos_solicitados:
            solicitado = por_codigo.get(codigo)
            if solicitado is None:
                resolucion.excluidos.append(
                    Exclusion(
                        codigo=codigo,
                        motivo="permiso_inexistente",
                        detalle="el permiso solicitado no existe en el catalogo",
                    )
                )
                continue
            propuesta.setdefault(
                solicitado.id,
                PermisoResuelto(
                    codigo=solicitado.codigo,
                    descripcion=solicitado.descripcion,
                    origen="directa",
                ),
            )

    # 2. ABAC: primero lo que suma, despues lo que niega
    for permiso_id, politicas in catalogo.politicas.items():
        if permiso_id in propuesta:
            continue
        for politica in politicas:
            if politica.efecto != "permitir":
                continue
            if evaluar(politica.condicion, atributos):
                permiso = catalogo.permisos[permiso_id]
                propuesta[permiso_id] = PermisoResuelto(
                    codigo=permiso.codigo,
                    descripcion=permiso.descripcion,
                    origen="abac_atributo",
                    politica=politica.codigo,
                )
                break

    for permiso_id in list(propuesta):
        for politica in catalogo.politicas.get(permiso_id, []):
            if politica.efecto != "negar":
                continue
            if not evaluar(politica.condicion, atributos):
                continue
            permiso = catalogo.permisos[permiso_id]
            del propuesta[permiso_id]
            resolucion.excluidos.append(
                Exclusion(
                    codigo=permiso.codigo,
                    motivo="politica_abac",
                    detalle=(
                        f"{politica.codigo} niega el permiso cuando "
                        f"{describir(politica.condicion)}"
                    ),
                )
            )
            break

    # 3. Validacion cruzada de Segregacion de Funciones
    vigentes: dict[int, AsignacionAcceso] = {}
    if conservar_vigentes and usuario.id is not None:
        vigentes = {
            a.permiso_id: a
            for a in sesion.scalars(
                select(AsignacionAcceso).where(
                    AsignacionAcceso.usuario_id == usuario.id,
                    AsignacionAcceso.activa.is_(True),
                )
            ).all()
        }
    resolucion.conservados = sorted(
        catalogo.permisos[pid].codigo for pid in vigentes if pid in catalogo.permisos
    )

    _validar_sod(catalogo, propuesta, set(vigentes), resolucion)

    resolucion.otorgados = sorted(propuesta.values(), key=lambda p: p.codigo)
    return resolucion


def _validar_sod(
    catalogo: _Catalogo,
    propuesta: dict[int, PermisoResuelto],
    vigentes: set[int],
    resolucion: Resolucion,
) -> None:
    """Saca de la propuesta todo lo que genere una incompatibilidad.

    Se recorre hasta que no queden conflictos, porque quitar un permiso no
    puede crear uno nuevo pero un mismo permiso puede estar en conflicto con
    varios a la vez. Lo que se retira es siempre el permiso propuesto, nunca
    el vigente: revocar un acceso que la persona ya tiene es una decision
    distinta, con su propio circuito, y no algo que un alta deba resolver de
    costado.
    """
    while True:
        conflicto = _primer_conflicto(catalogo, propuesta, vigentes)
        if conflicto is None:
            return
        regla, permiso_id, contra_id = conflicto
        permiso = catalogo.permisos[permiso_id]
        contra = catalogo.permisos[contra_id]
        del propuesta[permiso_id]
        resolucion.excluidos.append(
            Exclusion(
                codigo=permiso.codigo,
                motivo="conflicto_sod",
                detalle=(
                    f"{regla.codigo}: incompatible con {contra.codigo}"
                    + (" que la persona ya tiene" if contra_id in vigentes else "")
                ),
                regla=regla.codigo,
                severidad=regla.severidad,
                permiso_en_conflicto=contra.codigo,
            )
        )


def _primer_conflicto(
    catalogo: _Catalogo, propuesta: dict[int, PermisoResuelto], vigentes: set[int]
) -> tuple[ReglaSoD, int, int] | None:
    """Devuelve (regla, permiso a excluir, permiso con el que choca).

    Ante un conflicto entre dos permisos propuestos se excluye el de mayor
    peso de riesgo: es el mas excepcional de los dos y el que mas conviene
    que pase por una aprobacion explicita.
    """
    todos = set(propuesta) | vigentes
    for regla in catalogo.reglas:
        a, b = regla.permiso_a_id, regla.permiso_b_id
        if a not in todos or b not in todos:
            continue
        if a in propuesta and b in propuesta:
            peso_a = catalogo.permisos[a].peso_riesgo
            peso_b = catalogo.permisos[b].peso_riesgo
            excluir, contra = (a, b) if peso_a >= peso_b else (b, a)
            return regla, excluir, contra
        if a in propuesta:
            return regla, a, b
        if b in propuesta:
            return regla, b, a
    return None


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


def registrar_solicitud(
    sesion: Session,
    usuario: Usuario,
    resolucion: Resolucion,
    solicitada_por: str,
) -> SolicitudAlta:
    """Guarda la resolucion como una solicitud pendiente de aprobacion."""
    solicitud = SolicitudAlta(
        usuario_id=usuario.id,
        puesto_solicitado=resolucion.puesto,
        area_solicitada=resolucion.area,
        permisos_propuestos=resolucion.como_diccionario(),
        conflictos_detectados=(
            {
                "cantidad": sum(
                    1 for e in resolucion.excluidos if e.motivo == "conflicto_sod"
                ),
                "reglas": sorted(
                    {e.regla for e in resolucion.excluidos if e.regla is not None}
                ),
            }
            if resolucion.tiene_conflictos
            else None
        ),
        estado=EstadoSolicitud.PENDIENTE,
        solicitada_por=solicitada_por,
    )
    sesion.add(solicitud)
    sesion.flush()
    sesion.add(
        EventoAuditoria(
            actor=solicitada_por,
            accion="registrar_solicitud",
            entidad="solicitud_alta",
            entidad_id=solicitud.id,
            detalle={
                "legajo": resolucion.legajo,
                "puesto": resolucion.puesto,
                "otorgados": len(resolucion.otorgados),
                "excluidos": len(resolucion.excluidos),
                "conflictos": resolucion.tiene_conflictos,
            },
        )
    )
    sesion.flush()
    return solicitud


def resolver_solicitud(
    sesion: Session,
    solicitud: SolicitudAlta,
    aprobada: bool,
    resuelta_por: str,
) -> SolicitudAlta:
    """Aprueba o rechaza una solicitud.

    Nadie puede aprobar lo que el mismo origino. Es la misma separacion de
    funciones que el sistema detecta en las organizaciones que analiza, y
    dejarla afuera del propio sistema seria una incoherencia que la defensa
    senalaria con razon.
    """
    if aprobada and solicitud.solicitada_por == resuelta_por:
        raise PermissionError(
            "quien origina una solicitud no puede aprobarla: "
            f"{resuelta_por} la solicito y pretende aprobarla"
        )
    if solicitud.estado != EstadoSolicitud.PENDIENTE:
        raise ValueError(f"la solicitud ya fue {solicitud.estado}")

    from app.nucleo.modelos import ahora

    solicitud.estado = (
        EstadoSolicitud.APROBADA if aprobada else EstadoSolicitud.RECHAZADA
    )
    solicitud.resuelta_por = resuelta_por
    solicitud.resuelta_en = ahora()
    sesion.add(
        EventoAuditoria(
            actor=resuelta_por,
            accion="resolver_solicitud",
            entidad="solicitud_alta",
            entidad_id=solicitud.id,
            detalle={"estado": solicitud.estado},
        )
    )
    sesion.flush()
    return solicitud


@dataclass
class CambioPropuesto:
    """Una modificacion concreta a aplicar sobre la plataforma destino."""

    permiso_codigo: str
    accion: str  # otorgar | revocar
    origen: str
    rol: str | None = None


def resolucion_a_permisos(resolucion: Resolucion) -> list[CambioPropuesto]:
    """Traduce la resolucion al delta que hay que aplicar.

    Es la frontera entre el motor de politicas y la capa de generacion de
    scripts: lo que cruza no es una intencion sino una lista cerrada de
    cambios, cada uno con su permiso y su accion. El modelo de lenguaje
    recibe esto, no la solicitud original, y por eso no puede decidir
    accesos aunque quisiera.

    El delta se calcula contra lo que la propia resolucion considero
    vigente, que en un alta nueva es nada y en un cambio de puesto es lo que
    la persona ya tenia. No se vuelve a consultar la base: si lo hiciera, el
    conjunto que se le pasa al modelo podria no coincidir con el que se
    valido, y ahi se abriria una grieta entre la decision y lo que se
    ejecuta.
    """
    vigentes = set(resolucion.conservados)
    return [
        CambioPropuesto(
            permiso_codigo=p.codigo,
            accion="otorgar",
            origen=p.origen,
            rol=p.rol,
        )
        for p in resolucion.otorgados
        if p.codigo not in vigentes
    ]


def resumen(resolucion: Resolucion) -> dict[str, Any]:
    """Vista compacta para la pantalla de revision."""
    por_motivo: dict[str, int] = defaultdict(int)
    for exclusion in resolucion.excluidos:
        por_motivo[exclusion.motivo] += 1
    criticos = [
        e
        for e in resolucion.excluidos
        if e.severidad in (Severidad.CRITICA, Severidad.ALTA)
    ]
    return {
        "legajo": resolucion.legajo,
        "puesto": resolucion.puesto,
        "roles": resolucion.roles,
        "perfil_inferido": resolucion.perfil_inferido,
        "otorgados": len(resolucion.otorgados),
        "excluidos": len(resolucion.excluidos),
        "por_motivo": dict(por_motivo),
        "conflictos_criticos": len(criticos),
        "requiere_atencion": bool(criticos),
    }
