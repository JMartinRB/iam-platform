"""Generador de los conjuntos de datos sinteticos D-1, D-2 y D-3.

El generador construye una organizacion ficticia completa y despues planta
una cantidad conocida de hallazgos en cada una de las cinco categorias que
detecta el modulo de analisis. Esa cantidad queda registrada en
``manifiesto.json``, que es la verdad de referencia contra la cual se miden
la precision y la exhaustividad del motor de deteccion (indicador I-02 del
plan de pruebas).

Dos cuidados sostienen esa medicion:

1. Los usuarios que no fueron elegidos para un hallazgo se construyen de
   forma que no lo tengan por accidente. Un activo sin hallazgo plantado
   siempre tiene un ultimo acceso dentro de la ventana vigente, una baja sin
   hallazgo plantado nunca conserva asignaciones activas, y las
   combinaciones de permisos incompatibles se eliminan de quienes no fueron
   marcados.
2. Las categorias plantadas son mutuamente excluyentes por usuario, asi que
   un hallazgo detectado se puede atribuir sin ambiguedad.

Sobre esa base limpia se agrega ruido deliberado: nomenclaturas
inconsistentes en el codigo de permiso, usuarios repetidos y filas
malformadas. El ruido tambien queda declarado en el manifiesto para que las
pruebas puedan verificar que el importador lo resuelve como corresponde.

Uso:
    python -m herramientas.generar_dataset --perfil D-2
    python -m herramientas.generar_dataset --perfil D-3 --salida ../datasets
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from herramientas.catalogo import (
    APLICACIONES,
    FUNCIONES,
    PERFILES_ROL,
    PUESTOS,
    REGLAS_SOD,
    UBICACIONES,
)

SEMILLA_POR_DEFECTO = 20260401
DIAS_VENTANA_USO = 90  # coincide con DIAS_CUENTA_DORMIDA de la configuracion


@dataclass(frozen=True)
class Perfil:
    """Parametros de tamano de un conjunto de datos."""

    id: str
    descripcion: str
    usuarios: int
    asignaciones: int
    aplicaciones: int
    # Proporcion de usuarios marcados para cada categoria de hallazgo.
    tasas: dict[str, float] = field(
        default_factory=lambda: {
            "cuenta_dormida": 0.060,
            "cuenta_huerfana": 0.020,
            "sobre_privilegio": 0.030,
            "conflicto_sod": 0.040,
            "privilege_creep": 0.030,
        }
    )
    # Proporcion de bajas en la poblacion (incluye las huerfanas).
    tasa_bajas: float = 0.05
    # Cuantas veces el promedio tiene un usuario con sobreprivilegio plantado.
    factor_sobre_privilegio: float = 2.5
    # Tope de la poblacion limpia, como multiplo del promedio. Tiene que
    # quedar por debajo del umbral del detector (dos veces la mediana del
    # puesto) para que nadie aparezca como hallazgo sin haber sido plantado.
    tope_poblacion_limpia: float = 1.6


PERFILES: dict[str, Perfil] = {
    "D-1": Perfil("D-1", "Organizacion chica, estructura simple", 500, 5_000, 6),
    "D-2": Perfil("D-2", "Organizacion mediana con varias areas", 2_000, 20_000, 10),
    "D-3": Perfil("D-3", "Organizacion grande, volumen objetivo", 5_000, 50_000, 14),
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def sin_tildes(texto: str) -> str:
    """Normaliza a ASCII, que es lo que suele devolver un export de IAM."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def correo(nombre: str, apellido: str, n: int) -> str:
    base = f"{nombre[0]}{apellido}".lower()
    return f"{sin_tildes(base)}{n}@empresa-demo.test"


def deformar_codigo(codigo: str, rnd: random.Random) -> str:
    """Devuelve una variante del codigo con otra nomenclatura.

    Reproduce el problema habitual de los exports reales, donde el mismo
    permiso aparece escrito de maneras distintas segun la plataforma que lo
    exporto. El importador tiene que normalizarlas a un unico codigo.
    """
    variantes = [
        codigo.lower(),
        codigo.replace("_", "-"),
        codigo.replace("_", "."),
        f" {codigo} ",
        codigo.title(),
    ]
    return rnd.choice(variantes)


# ---------------------------------------------------------------------------
# Generador
# ---------------------------------------------------------------------------


class Generador:
    """Construye un conjunto de datos y su manifiesto de verdad de referencia."""

    def __init__(
        self,
        perfil: Perfil,
        semilla: int = SEMILLA_POR_DEFECTO,
        fecha_corte: date | None = None,
    ) -> None:
        self.perfil = perfil
        self.semilla = semilla
        self.hoy = fecha_corte or date(2026, 10, 1)
        self.rnd = random.Random(semilla)
        # Faker aporta los nombres y las localidades; el resto de la
        # estructura la arma este generador. Se siembra con la misma semilla
        # para que el conjunto completo sea reproducible.
        self.faker = Faker("es_AR")
        Faker.seed(semilla)

        self.aplicaciones: list[dict] = []
        self.permisos: list[dict] = []
        self.roles: list[dict] = []
        self.roles_permisos: list[dict] = []
        self.usuarios: list[dict] = []
        self.asignaciones: list[dict] = []
        self.reglas_sod: list[dict] = []

        # indices auxiliares
        self._permiso_por_codigo: dict[str, dict] = {}
        self._permisos_por_funcion: dict[str, list[dict]] = {}
        self._rol_por_clave: dict[tuple[str, str], dict] = {}
        self._permisos_de_rol: dict[str, list[str]] = {}
        self._pares_sod: set[frozenset[str]] = set()

        self.plantados: dict[str, list[dict]] = {
            cat: [] for cat in self.perfil.tasas
        }
        self.excepciones_diseno: list[dict] = []
        self.ruido: dict[str, int] = {
            "codigos_con_nomenclatura_alternativa": 0,
            "usuarios_duplicados": 0,
            "campos_opcionales_vacios": 0,
            "filas_malformadas": 0,
        }
        self.filas_malformadas: list[dict] = []
        self.conflictos_removidos = 0

    # -- catalogo -----------------------------------------------------------

    def construir_catalogo(self) -> None:
        apps = APLICACIONES[: self.perfil.aplicaciones]
        for codigo, nombre, criticidad, tecnologia, funciones in apps:
            self.aplicaciones.append(
                {
                    "codigo": codigo,
                    "nombre": nombre,
                    "criticidad": criticidad,
                    "propietario": f"Responsable {nombre}",
                    "tecnologia": tecnologia,
                }
            )
            for funcion in funciones:
                descripcion, nivel, peso = FUNCIONES[funcion]
                permiso = {
                    "aplicacion_codigo": codigo,
                    "codigo": f"{codigo}_{funcion}",
                    "funcion": funcion,
                    "descripcion": f"{descripcion} en {nombre}",
                    "nivel": nivel,
                    "peso_riesgo": peso,
                }
                self.permisos.append(permiso)
                self._permiso_por_codigo[permiso["codigo"]] = permiso
                self._permisos_por_funcion.setdefault(funcion, []).append(permiso)

        self._construir_reglas_sod()
        self._construir_roles()

    def _construir_reglas_sod(self) -> None:
        """Instancia las reglas del catalogo sobre los permisos existentes.

        Se generan dos variantes de cada regla: la intra aplicacion, que es
        la mas frecuente, y una cruzada entre dos aplicaciones distintas que
        expongan las funciones en conflicto. La variante cruzada es la que
        justifica la validacion transversal descrita en el capitulo 4.
        """
        n = 0
        for funcion_a, funcion_b, severidad, fundamento in REGLAS_SOD:
            permisos_a = self._permisos_por_funcion.get(funcion_a, [])
            permisos_b = self._permisos_por_funcion.get(funcion_b, [])
            if not permisos_a or not permisos_b:
                continue

            # intra aplicacion
            for pa in permisos_a:
                pb = next(
                    (
                        p
                        for p in permisos_b
                        if p["aplicacion_codigo"] == pa["aplicacion_codigo"]
                    ),
                    None,
                )
                if pb is None:
                    continue
                n += 1
                self._agregar_regla(n, pa, pb, severidad, fundamento, "intra")

            # cruzada entre aplicaciones distintas
            cruzada = next(
                (
                    (pa, pb)
                    for pa in permisos_a
                    for pb in permisos_b
                    if pa["aplicacion_codigo"] != pb["aplicacion_codigo"]
                ),
                None,
            )
            if cruzada is not None:
                n += 1
                self._agregar_regla(
                    n, cruzada[0], cruzada[1], severidad, fundamento, "cruzada"
                )

    def _agregar_regla(
        self,
        n: int,
        pa: dict,
        pb: dict,
        severidad: str,
        fundamento: str,
        alcance: str,
    ) -> None:
        par = frozenset({pa["codigo"], pb["codigo"]})
        if par in self._pares_sod:
            return
        self._pares_sod.add(par)
        self.reglas_sod.append(
            {
                "codigo": f"SOD-{n:03d}",
                "descripcion": (
                    f"{FUNCIONES[pa['funcion']][0]} y {FUNCIONES[pb['funcion']][0]} "
                    f"en la misma identidad ({alcance})"
                ),
                "permiso_a": pa["codigo"],
                "permiso_b": pb["codigo"],
                "severidad": severidad,
                "fundamento": fundamento,
                "activa": True,
            }
        )

    def _conflictos(self, codigos: set[str]) -> list[dict]:
        """Devuelve las reglas que un conjunto de permisos infringe."""
        return [
            r
            for r in self.reglas_sod
            if r["permiso_a"] in codigos and r["permiso_b"] in codigos
        ]

    def _construir_roles(self) -> None:
        """Crea un rol por cada combinacion aplicacion-perfil en uso.

        Si la interseccion entre el perfil y las funciones de la aplicacion
        infringe una regla de Segregacion de Funciones, se descarta el
        permiso de menor peso y se registra la decision. Un rol que nace con
        un conflicto adentro haria que todos sus asignatarios aparezcan como
        hallazgo, y eso arruinaria la medicion.
        """
        usados = {
            (app, perfil)
            for _, _, roles in PUESTOS
            for app, perfil in roles
            if any(a["codigo"] == app for a in self.aplicaciones)
        }
        for app, perfil in sorted(usados):
            funciones_app = {
                p["funcion"] for p in self.permisos if p["aplicacion_codigo"] == app
            }
            funciones = [f for f in PERFILES_ROL[perfil] if f in funciones_app]
            codigos = {f"{app}_{f}" for f in funciones}

            for regla in self._conflictos(codigos):
                a, b = regla["permiso_a"], regla["permiso_b"]
                descartado = min(
                    (a, b), key=lambda c: self._permiso_por_codigo[c]["peso_riesgo"]
                )
                codigos.discard(descartado)
                self.excepciones_diseno.append(
                    {
                        "tipo": "permiso_excluido_de_rol",
                        "rol": f"{app}_{perfil}",
                        "permiso": descartado,
                        "regla": regla["codigo"],
                    }
                )

            rol = {
                "aplicacion_codigo": app,
                "codigo": f"{app}_{perfil}",
                "nombre": f"{perfil.replace('_', ' ').title()} de {app}",
                "perfil": perfil,
            }
            self.roles.append(rol)
            self._rol_por_clave[(app, perfil)] = rol
            self._permisos_de_rol[rol["codigo"]] = sorted(codigos)
            for codigo in sorted(codigos):
                self.roles_permisos.append(
                    {"rol_codigo": rol["codigo"], "permiso_codigo": codigo}
                )

    # -- poblacion ----------------------------------------------------------

    def _puestos_disponibles(self) -> list[tuple[str, str, list[tuple[str, str]]]]:
        codigos = {a["codigo"] for a in self.aplicaciones}
        disponibles = []
        for area, puesto, roles in PUESTOS:
            vigentes = [(app, perfil) for app, perfil in roles if app in codigos]
            if vigentes:
                disponibles.append((area, puesto, vigentes))
        return disponibles

    def construir_usuarios(self) -> None:
        total = self.perfil.usuarios
        puestos = self._puestos_disponibles()
        cantidad_bajas = int(total * self.perfil.tasa_bajas)

        for i in range(1, total + 1):
            area, puesto, roles = puestos[(i - 1) % len(puestos)]
            nombre = self.faker.first_name()
            apellido = self.faker.last_name()
            ingreso = self.hoy - timedelta(days=self.rnd.randint(120, 4_000))
            self.usuarios.append(
                {
                    "legajo": f"U{i:05d}",
                    "nombre": f"{nombre} {apellido}",
                    "correo": correo(nombre, apellido, i),
                    "area": area,
                    "puesto": puesto,
                    "ubicacion": self.rnd.choice(UBICACIONES),
                    "jefe_legajo": "",
                    "estado": "activo",
                    "fecha_ingreso": ingreso.isoformat(),
                    "fecha_baja": "",
                    "ultimo_acceso": "",
                    "_roles": roles,
                    "_categoria": "",
                }
            )

        # jefes: el primer usuario de cada area oficia de responsable
        jefes: dict[str, str] = {}
        for u in self.usuarios:
            jefes.setdefault(u["area"], u["legajo"])
        for u in self.usuarios:
            if jefes[u["area"]] != u["legajo"]:
                u["jefe_legajo"] = jefes[u["area"]]

        self._marcar_categorias(cantidad_bajas)
        self._fechar_accesos()

    def _marcar_categorias(self, cantidad_bajas: int) -> None:
        """Reparte las categorias de hallazgo entre usuarios sin solapamiento."""
        indices = list(range(len(self.usuarios)))
        self.rnd.shuffle(indices)
        cursor = 0

        # Las bajas se eligen primero: la categoria huerfana vive dentro de
        # ese grupo, porque una cuenta huerfana es por definicion una baja
        # con accesos activos.
        bajas = indices[:cantidad_bajas]
        cursor = cantidad_bajas
        for i in bajas:
            u = self.usuarios[i]
            u["estado"] = "baja"
            baja = self.hoy - timedelta(days=self.rnd.randint(20, 400))
            u["fecha_baja"] = baja.isoformat()

        huerfanas = int(self.perfil.usuarios * self.perfil.tasas["cuenta_huerfana"])
        for i in bajas[:huerfanas]:
            self.usuarios[i]["_categoria"] = "cuenta_huerfana"

        for categoria in (
            "cuenta_dormida",
            "sobre_privilegio",
            "conflicto_sod",
            "privilege_creep",
        ):
            cantidad = int(self.perfil.usuarios * self.perfil.tasas[categoria])
            elegidos = indices[cursor : cursor + cantidad]
            cursor += cantidad
            for i in elegidos:
                self.usuarios[i]["_categoria"] = categoria

    def _fechar_accesos(self) -> None:
        """Asigna el ultimo acceso cuidando que no genere hallazgos fortuitos."""
        limite = DIAS_VENTANA_USO
        for u in self.usuarios:
            if u["estado"] == "baja":
                # Para una baja el ultimo acceso es anterior a la fecha de baja.
                fecha_baja = date.fromisoformat(u["fecha_baja"])
                acceso = fecha_baja - timedelta(days=self.rnd.randint(0, 30))
                u["ultimo_acceso"] = acceso.isoformat()
                continue

            if u["_categoria"] == "cuenta_dormida":
                dias = self.rnd.randint(limite + 15, limite + 600)
            else:
                # Margen de 10 dias por debajo del umbral para que un
                # corrimiento de la fecha de corte no convierta a un usuario
                # limpio en hallazgo.
                dias = self.rnd.randint(0, limite - 10)
            u["ultimo_acceso"] = (self.hoy - timedelta(days=dias)).isoformat()

    # -- asignaciones -------------------------------------------------------

    def construir_asignaciones(self) -> None:
        promedio = self.perfil.asignaciones / self.perfil.usuarios
        otorgados_por_legajo: dict[str, dict[str, dict]] = {}

        for u in self.usuarios:
            base_roles: list[str] = [
                self._rol_por_clave[(app, perfil)]["codigo"]
                for app, perfil in u["_roles"]
                if (app, perfil) in self._rol_por_clave
            ]
            otorgados: dict[str, dict] = {}

            # 1. linea de base por rol (RBAC)
            for rol_codigo in base_roles:
                for permiso_codigo in self._permisos_de_rol[rol_codigo]:
                    if permiso_codigo in otorgados:
                        continue
                    otorgados[permiso_codigo] = self._asignacion(
                        u, permiso_codigo, "rbac_rol", rol_codigo
                    )

            # 2. permisos directos hasta alcanzar el volumen objetivo
            objetivo = self._objetivo_usuario(u, promedio)
            candidatos = self._candidatos_directos(u, otorgados)
            for permiso_codigo in candidatos:
                if len(otorgados) >= objetivo:
                    break
                otorgados[permiso_codigo] = self._asignacion(
                    u, permiso_codigo, "directa", ""
                )

            # 3. limpieza: nadie queda en conflicto por casualidad. Se hace
            #    antes de plantar para no tocar lo que se planta a proposito.
            self._limpiar_conflictos(otorgados)

            # 4. hallazgos que se plantan sobre las asignaciones
            if u["_categoria"] == "conflicto_sod":
                self._plantar_sod(u, otorgados)
            elif u["_categoria"] == "privilege_creep":
                self._plantar_creep(u, otorgados)
            elif u["_categoria"] == "sobre_privilegio":
                self._plantar_sobre_privilegio(u, otorgados, objetivo)

            # 5. estado de las asignaciones segun el estado del usuario
            if u["estado"] == "baja":
                activas = u["_categoria"] == "cuenta_huerfana"
                for a in otorgados.values():
                    a["activa"] = activas

            otorgados_por_legajo[u["legajo"]] = otorgados

            if u["_categoria"] == "cuenta_dormida":
                self.plantados["cuenta_dormida"].append(
                    {
                        "legajo": u["legajo"],
                        "dias_sin_uso": (
                            self.hoy - date.fromisoformat(u["ultimo_acceso"])
                        ).days,
                    }
                )
            elif u["_categoria"] == "cuenta_huerfana":
                self.plantados["cuenta_huerfana"].append(
                    {
                        "legajo": u["legajo"],
                        "fecha_baja": u["fecha_baja"],
                        "asignaciones_activas": len(otorgados),
                    }
                )

        self._completar_volumen(otorgados_por_legajo)
        for u in self.usuarios:
            self.asignaciones.extend(otorgados_por_legajo[u["legajo"]].values())

    def _completar_volumen(self, otorgados_por_legajo: dict[str, dict]) -> None:
        """Lleva el total de asignaciones al volumen declarado para el perfil.

        La construccion por usuario queda corta respecto del objetivo porque
        la limpieza de conflictos retira asignaciones. El relleno se hace
        sobre usuarios activos sin hallazgo plantado y nunca introduce una
        combinacion en conflicto, asi que no mueve la verdad de referencia.
        """
        objetivo = self.perfil.asignaciones
        promedio = self.perfil.asignaciones / self.perfil.usuarios
        tope = self._tope_limpio(promedio)
        total = sum(len(v) for v in otorgados_por_legajo.values())
        candidatos = [
            u
            for u in self.usuarios
            if not u["_categoria"] and u["estado"] == "activo"
        ]
        if not candidatos:
            return

        intentos = 0
        tope_intentos = objetivo * 20
        while total < objetivo and intentos < tope_intentos:
            intentos += 1
            u = self.rnd.choice(candidatos)
            otorgados = otorgados_por_legajo[u["legajo"]]
            if len(otorgados) >= tope:
                continue
            permiso = self.rnd.choice(self.permisos)
            codigo = permiso["codigo"]
            if codigo in otorgados:
                continue
            if self._conflictos(set(otorgados) | {codigo}):
                continue
            otorgados[codigo] = self._asignacion(u, codigo, "directa", "")
            total += 1

    def _objetivo_usuario(self, usuario: dict, promedio: float) -> int:
        """Cantidad de permisos que deberia tener el usuario.

        La dispersion se mantiene baja a proposito: el detector de
        sobreprivilegio compara contra el percentil del puesto, asi que una
        poblacion muy dispersa produciria hallazgos que nadie planto.
        """
        if usuario["_categoria"] == "sobre_privilegio":
            return round(promedio * self.perfil.factor_sobre_privilegio)
        objetivo = self.rnd.gauss(promedio, promedio * 0.12)
        return max(2, min(round(objetivo), self._tope_limpio(promedio)))

    def _tope_limpio(self, promedio: float) -> int:
        return int(promedio * self.perfil.tope_poblacion_limpia)

    def _candidatos_directos(self, usuario: dict, otorgados: dict) -> list[str]:
        """Permisos plausibles para otorgar por fuera del rol.

        Se priorizan las aplicaciones que el usuario ya usa, que es el orden
        en que crecen los accesos en la practica: primero mas permisos en lo
        que ya se toca, despues sistemas nuevos.
        """
        apps_propias = {
            self._permiso_por_codigo[c]["aplicacion_codigo"] for c in otorgados
        }
        cercanos = [
            p["codigo"]
            for p in self.permisos
            if p["aplicacion_codigo"] in apps_propias and p["codigo"] not in otorgados
        ]
        lejanos = [
            p["codigo"]
            for p in self.permisos
            if p["aplicacion_codigo"] not in apps_propias
            and p["nivel"] != "administracion"
        ]
        resto = [
            p["codigo"]
            for p in self.permisos
            if p["codigo"] not in otorgados
            and p["codigo"] not in cercanos
            and p["codigo"] not in lejanos
        ]
        self.rnd.shuffle(cercanos)
        self.rnd.shuffle(lejanos)
        self.rnd.shuffle(resto)
        return cercanos + lejanos + resto

    def _asignacion(
        self, usuario: dict, permiso_codigo: str, origen: str, rol_codigo: str
    ) -> dict:
        ingreso = date.fromisoformat(usuario["fecha_ingreso"])
        dias_desde_ingreso = max((self.hoy - ingreso).days, 1)
        otorgamiento = ingreso + timedelta(
            days=self.rnd.randint(0, min(dias_desde_ingreso - 1, 900))
        )
        revision = self.hoy - timedelta(days=self.rnd.randint(10, 180))
        if revision < otorgamiento:
            revision = otorgamiento
        return {
            "legajo": usuario["legajo"],
            "aplicacion_codigo": self._permiso_por_codigo[permiso_codigo][
                "aplicacion_codigo"
            ],
            "permiso_codigo": permiso_codigo,
            "rol_codigo": rol_codigo,
            "origen": origen,
            "otorgado_por": usuario["jefe_legajo"] or "U00001",
            "fecha_otorgamiento": otorgamiento.isoformat(),
            "fecha_revision": revision.isoformat(),
            "activa": True,
        }

    def _plantar_sod(self, usuario: dict, otorgados: dict) -> None:
        """Otorga las dos mitades de una regla a un usuario marcado."""
        reglas = [r for r in self.reglas_sod if r["severidad"] in ("critica", "alta")]
        regla = self.rnd.choice(reglas)
        for codigo in (regla["permiso_a"], regla["permiso_b"]):
            if codigo not in otorgados:
                otorgados[codigo] = self._asignacion(usuario, codigo, "directa", "")
        self.plantados["conflicto_sod"].append(
            {
                "legajo": usuario["legajo"],
                "regla": regla["codigo"],
                "permisos": [regla["permiso_a"], regla["permiso_b"]],
                "severidad": regla["severidad"],
            }
        )

    def _plantar_creep(self, usuario: dict, otorgados: dict) -> None:
        """Simula accesos heredados de puestos anteriores.

        El rastro es el que deja un cambio de puesto sin revocacion: permisos
        otorgados de forma directa hace mas de un ano, nunca revisados y
        ajenos a cualquier rol del puesto actual.
        """
        propios = {
            c
            for app, perfil in usuario["_roles"]
            if (app, perfil) in self._rol_por_clave
            for c in self._permisos_de_rol[self._rol_por_clave[(app, perfil)]["codigo"]]
        }
        ajenos = [
            p["codigo"]
            for p in self.permisos
            if p["codigo"] not in propios and p["peso_riesgo"] >= 4
        ]
        self.rnd.shuffle(ajenos)
        heredados: list[str] = []
        for codigo in ajenos:
            if len(heredados) >= 4:
                break
            if codigo in otorgados and otorgados[codigo]["origen"] == "rbac_rol":
                continue
            # un acceso heredado no deberia generar ademas un conflicto de
            # SoD, porque el hallazgo pasaria a pertenecer a dos categorias
            if self._conflictos(set(otorgados) | {codigo}):
                continue
            otorgamiento = self.hoy - timedelta(days=self.rnd.randint(420, 1_200))
            otorgados[codigo] = {
                **self._asignacion(usuario, codigo, "directa", ""),
                "fecha_otorgamiento": otorgamiento.isoformat(),
                "fecha_revision": "",
            }
            heredados.append(codigo)
        self.plantados["privilege_creep"].append(
            {
                "legajo": usuario["legajo"],
                "permisos_heredados": heredados,
                "puesto_actual": usuario["puesto"],
            }
        )

    def _plantar_sobre_privilegio(
        self, usuario: dict, otorgados: dict, objetivo: int
    ) -> None:
        """Completa la acumulacion hasta el objetivo despues de la limpieza.

        El relleno se hace aca y no antes porque la limpieza de conflictos
        retira asignaciones, y un usuario que tenia que quedar con dos veces
        y media el promedio terminaba a veces apenas por encima de la
        mediana de su puesto, es decir por debajo del umbral que lo tiene
        que delatar.
        """
        candidatos = [p["codigo"] for p in self.permisos if p["codigo"] not in otorgados]
        self.rnd.shuffle(candidatos)
        for codigo in candidatos:
            if len(otorgados) >= objetivo:
                break
            if self._conflictos(set(otorgados) | {codigo}):
                continue
            otorgados[codigo] = self._asignacion(usuario, codigo, "directa", "")

        self.plantados["sobre_privilegio"].append(
            {
                "legajo": usuario["legajo"],
                "puesto": usuario["puesto"],
                "permisos": len(otorgados),
                "peso_total": sum(
                    self._permiso_por_codigo[c]["peso_riesgo"] for c in otorgados
                ),
            }
        )

    def _limpiar_conflictos(self, otorgados: dict) -> None:
        """Quita una mitad de cada conflicto no intencional."""
        while True:
            conflictos = self._conflictos(set(otorgados))
            if not conflictos:
                return
            regla = conflictos[0]
            # se conserva el permiso que viene del rol; si los dos vienen del
            # rol se descarta el de mayor peso, que es el mas excepcional
            a, b = regla["permiso_a"], regla["permiso_b"]
            de_rol_a = otorgados[a]["origen"] == "rbac_rol"
            de_rol_b = otorgados[b]["origen"] == "rbac_rol"
            if de_rol_a and not de_rol_b:
                descartado = b
            elif de_rol_b and not de_rol_a:
                descartado = a
            else:
                descartado = max(
                    (a, b), key=lambda c: self._permiso_por_codigo[c]["peso_riesgo"]
                )
            del otorgados[descartado]
            self.conflictos_removidos += 1

    # -- ruido --------------------------------------------------------------

    def agregar_ruido(self) -> None:
        """Introduce los defectos que tiene un export real.

        El ruido se concentra en usuarios sin hallazgo plantado, para que
        resolverlo mal no altere la verdad de referencia.
        """
        limpios = [u for u in self.usuarios if not u["_categoria"]]
        if not limpios:
            return

        # 1. nomenclatura inconsistente en el codigo de permiso
        cantidad = max(1, int(len(self.asignaciones) * 0.03))
        marcables = [
            a
            for a in self.asignaciones
            if not self._categoria_de(a["legajo"])
        ]
        for a in self.rnd.sample(marcables, min(cantidad, len(marcables))):
            a["permiso_codigo"] = deformar_codigo(a["permiso_codigo"], self.rnd)
            self.ruido["codigos_con_nomenclatura_alternativa"] += 1

        # 2. campos opcionales vacios
        for u in self.rnd.sample(limpios, max(1, int(len(limpios) * 0.04))):
            u["ubicacion"] = ""
            self.ruido["campos_opcionales_vacios"] += 1

        # 3. usuarios repetidos con variacion ortografica del nombre
        duplicables = self.rnd.sample(limpios, max(1, int(len(limpios) * 0.01)))
        for u in duplicables:
            copia = dict(u)
            copia["nombre"] = u["nombre"].upper()
            copia["_duplicado"] = True
            self.usuarios.append(copia)
            self.ruido["usuarios_duplicados"] += 1

        # 4. filas malformadas: el importador tiene que rechazarlas y
        #    reportar el motivo (caso de aceptacion CP-01). Se escriben sobre
        #    usuarios sin hallazgo plantado, para que un rechazo mal resuelto
        #    no se confunda con una falla del motor de deteccion.
        permiso_valido = self.permisos[0]["codigo"]
        sin_marca = [u for u in limpios if not u.get("_duplicado")][:3]
        while len(sin_marca) < 3:
            sin_marca.append(limpios[0])
        self.filas_malformadas = [
            {
                "legajo": "",
                "aplicacion_codigo": self.aplicaciones[0]["codigo"],
                "permiso_codigo": permiso_valido,
                "rol_codigo": "",
                "origen": "directa",
                "otorgado_por": "U00001",
                "fecha_otorgamiento": self.hoy.isoformat(),
                "fecha_revision": "",
                "activa": "true",
                "_motivo": "legajo vacio",
            },
            {
                "legajo": "U99999",
                "aplicacion_codigo": self.aplicaciones[0]["codigo"],
                "permiso_codigo": permiso_valido,
                "rol_codigo": "",
                "origen": "directa",
                "otorgado_por": "U00001",
                "fecha_otorgamiento": self.hoy.isoformat(),
                "fecha_revision": "",
                "activa": "true",
                "_motivo": "legajo inexistente",
            },
            {
                "legajo": sin_marca[0]["legajo"],
                "aplicacion_codigo": self.aplicaciones[0]["codigo"],
                "permiso_codigo": "PERMISO_QUE_NO_EXISTE",
                "rol_codigo": "",
                "origen": "directa",
                "otorgado_por": "U00001",
                "fecha_otorgamiento": self.hoy.isoformat(),
                "fecha_revision": "",
                "activa": "true",
                "_motivo": "permiso inexistente",
            },
            {
                "legajo": sin_marca[1]["legajo"],
                "aplicacion_codigo": self.aplicaciones[0]["codigo"],
                "permiso_codigo": permiso_valido,
                "rol_codigo": "",
                "origen": "transferencia",
                "otorgado_por": "U00001",
                "fecha_otorgamiento": self.hoy.isoformat(),
                "fecha_revision": "",
                "activa": "true",
                "_motivo": "origen fuera del dominio permitido",
            },
            {
                "legajo": sin_marca[2]["legajo"],
                "aplicacion_codigo": self.aplicaciones[0]["codigo"],
                "permiso_codigo": permiso_valido,
                "rol_codigo": "",
                "origen": "directa",
                "otorgado_por": "U00001",
                "fecha_otorgamiento": "31/02/2026",
                "fecha_revision": "",
                "activa": "true",
                "_motivo": "fecha invalida",
            },
        ]
        self.ruido["filas_malformadas"] = len(self.filas_malformadas)

    def _categoria_de(self, legajo: str) -> str:
        indice = int(legajo[1:]) - 1
        if 0 <= indice < len(self.usuarios):
            return self.usuarios[indice]["_categoria"]
        return ""

    # -- salida -------------------------------------------------------------

    def escribir(self, destino: Path) -> dict:
        carpeta = destino / self.perfil.id
        carpeta.mkdir(parents=True, exist_ok=True)

        self._csv(
            carpeta / "aplicaciones.csv",
            ["codigo", "nombre", "criticidad", "propietario", "tecnologia"],
            self.aplicaciones,
        )
        self._csv(
            carpeta / "permisos.csv",
            ["aplicacion_codigo", "codigo", "descripcion", "nivel", "peso_riesgo"],
            self.permisos,
        )
        self._csv(
            carpeta / "roles.csv",
            ["aplicacion_codigo", "codigo", "nombre"],
            self.roles,
        )
        self._csv(
            carpeta / "roles_permisos.csv",
            ["rol_codigo", "permiso_codigo"],
            self.roles_permisos,
        )
        self._csv(
            carpeta / "usuarios.csv",
            [
                "legajo",
                "nombre",
                "correo",
                "area",
                "puesto",
                "ubicacion",
                "jefe_legajo",
                "estado",
                "fecha_ingreso",
                "fecha_baja",
                "ultimo_acceso",
            ],
            self.usuarios,
        )
        self._csv(
            carpeta / "asignaciones.csv",
            [
                "legajo",
                "aplicacion_codigo",
                "permiso_codigo",
                "rol_codigo",
                "origen",
                "otorgado_por",
                "fecha_otorgamiento",
                "fecha_revision",
                "activa",
            ],
            self.asignaciones + self.filas_malformadas,
        )
        self._csv(
            carpeta / "reglas_sod.csv",
            [
                "codigo",
                "descripcion",
                "permiso_a",
                "permiso_b",
                "severidad",
                "fundamento",
                "activa",
            ],
            self.reglas_sod,
        )

        manifiesto = self.manifiesto()
        (carpeta / "manifiesto.json").write_text(
            json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return manifiesto

    @staticmethod
    def _csv(ruta: Path, columnas: list[str], filas: list[dict]) -> None:
        with ruta.open("w", newline="", encoding="utf-8") as f:
            escritor = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
            escritor.writeheader()
            for fila in filas:
                escritor.writerow({c: fila.get(c, "") for c in columnas})

    def manifiesto(self) -> dict:
        activos = sum(1 for u in self.usuarios if u["estado"] == "activo")
        return {
            "perfil": self.perfil.id,
            "descripcion": self.perfil.descripcion,
            "semilla": self.semilla,
            "fecha_corte": self.hoy.isoformat(),
            "dias_ventana_uso": DIAS_VENTANA_USO,
            "criterio_sobre_privilegio": {
                "regla": "permisos activos >= 2 veces la mediana del puesto",
                "factor_plantado": self.perfil.factor_sobre_privilegio,
                "tope_poblacion_limpia": self.perfil.tope_poblacion_limpia,
            },
            "volumen": {
                "aplicaciones": len(self.aplicaciones),
                "permisos": len(self.permisos),
                "roles": len(self.roles),
                "roles_permisos": len(self.roles_permisos),
                "usuarios": len(self.usuarios),
                "usuarios_activos": activos,
                "usuarios_baja": len(self.usuarios) - activos,
                "asignaciones": len(self.asignaciones),
                "reglas_sod": len(self.reglas_sod),
                "filas_totales_asignaciones": len(self.asignaciones)
                + len(self.filas_malformadas),
            },
            "hallazgos_plantados": {
                categoria: {"cantidad": len(items), "casos": items}
                for categoria, items in self.plantados.items()
            },
            "totales_plantados": {
                categoria: len(items) for categoria, items in self.plantados.items()
            },
            "ruido": self.ruido,
            "motivos_filas_malformadas": [
                f["_motivo"] for f in self.filas_malformadas
            ],
            "excepciones_diseno": self.excepciones_diseno,
            "conflictos_no_intencionales_removidos": self.conflictos_removidos,
            "notas": [
                "Los datos son sinteticos. No provienen de ninguna organizacion real.",
                "Las categorias plantadas son excluyentes por usuario.",
                "El detector de sobreprivilegio compara contra el percentil del "
                "puesto, por lo que puede senalar casos limite que no figuran "
                "aca; esa diferencia es justamente lo que mide la precision.",
            ],
        }


def generar(
    perfil_id: str,
    destino: Path,
    semilla: int = SEMILLA_POR_DEFECTO,
    fecha_corte: date | None = None,
) -> dict:
    """Genera un conjunto completo y devuelve su manifiesto."""
    if perfil_id not in PERFILES:
        raise ValueError(f"Perfil desconocido: {perfil_id}. Opciones: {list(PERFILES)}")
    g = Generador(PERFILES[perfil_id], semilla=semilla, fecha_corte=fecha_corte)
    g.construir_catalogo()
    g.construir_usuarios()
    g.construir_asignaciones()
    g.agregar_ruido()
    return g.escribir(destino)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--perfil", default="D-2", choices=sorted(PERFILES))
    parser.add_argument("--salida", default="datasets")
    parser.add_argument("--semilla", type=int, default=SEMILLA_POR_DEFECTO)
    parser.add_argument("--todos", action="store_true", help="genera D-1, D-2 y D-3")
    args = parser.parse_args()

    destino = Path(args.salida)
    perfiles = sorted(PERFILES) if args.todos else [args.perfil]
    for perfil_id in perfiles:
        manifiesto = generar(perfil_id, destino, semilla=args.semilla)
        v = manifiesto["volumen"]
        print(
            f"{perfil_id}: {v['usuarios']} usuarios, {v['asignaciones']} asignaciones, "
            f"{v['reglas_sod']} reglas SoD"
        )
        for categoria, cantidad in manifiesto["totales_plantados"].items():
            print(f"   {categoria}: {cantidad}")


if __name__ == "__main__":
    main()
