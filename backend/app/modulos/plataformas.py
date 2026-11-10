"""Definicion de las plataformas destino y de lo que se les permite hacer.

Cada plataforma declara tres cosas: que operaciones estan permitidas, que
patrones estan prohibidos aunque parezcan permitidos, y como se extraen del
script los identificadores que despues hay que contrastar contra la
decision del motor de reglas.

Las listas son cortas a proposito. Una lista blanca que crece hasta cubrir
todo lo que el modelo podria querer escribir deja de ser un control y pasa a
ser documentacion. Lo que el sistema necesita hacer es acotado: agregar y
quitar miembros de grupos, habilitar y deshabilitar cuentas, y modificar
atributos puntuales.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plataforma:
    """Reglas de una plataforma destino."""

    codigo: str
    nombre: str
    #: Operaciones admitidas. En PowerShell son cmdlets; en Okta, pares de
    #: metodo y patron de ruta.
    lista_blanca: frozenset[str]
    #: Patrones prohibidos, con el motivo que se muestra al analista.
    lista_negra: tuple[tuple[str, str], ...]
    #: Expresiones que extraen del script los identificadores a contrastar.
    extractores: tuple[str, ...] = field(default=())

    def operaciones(self, script: str) -> list[str]:
        raise NotImplementedError

    def identificadores(self, script: str) -> set[str]:
        encontrados: set[str] = set()
        for patron in self.extractores:
            for coincidencia in re.finditer(patron, script, re.IGNORECASE):
                for grupo in coincidencia.groups():
                    if grupo:
                        encontrados.add(grupo.strip())
        return encontrados


class PowerShell(Plataforma):
    """Active Directory a traves de los cmdlets del modulo ActiveDirectory."""

    def operaciones(self, script: str) -> list[str]:
        """Devuelve los cmdlets invocados.

        Un cmdlet es Verbo-Sustantivo al principio de una sentencia. Se
        ignoran las apariciones dentro de cadenas y comentarios, que no se
        ejecutan.
        """
        sin_comentarios = re.sub(r"#.*$", "", script, flags=re.MULTILINE)
        sin_cadenas = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "''", sin_comentarios)
        return [
            coincidencia.group(1)
            for coincidencia in re.finditer(
                r"(?:^|[|;{(]|\$\()\s*([A-Za-z]+-[A-Za-z]+)\b",
                sin_cadenas,
                re.MULTILINE,
            )
        ]


class Okta(Plataforma):
    """Llamadas REST a la API de administracion de Okta."""

    def operaciones(self, script: str) -> list[str]:
        """Devuelve cada llamada canonizada contra los patrones permitidos.

        Canonizar contra los propios patrones, en lugar de adivinar que
        segmento de la ruta es un identificador, evita el error de tratar
        como identificador a un segmento fijo como ``groups``. Una llamada
        que no coincide con ningun patron se devuelve tal cual, y asi cae en
        la lista blanca, que es donde tiene que caer.
        """
        import json

        try:
            llamadas = json.loads(script)
        except json.JSONDecodeError:
            return []
        if isinstance(llamadas, dict):
            llamadas = [llamadas]
        if not isinstance(llamadas, list):
            return []

        operaciones = []
        for llamada in llamadas:
            if not isinstance(llamada, dict):
                continue
            metodo = str(llamada.get("metodo", llamada.get("method", ""))).upper()
            ruta = str(llamada.get("ruta", llamada.get("path", "")))
            operaciones.append(self._canonizar(metodo, ruta))
        return operaciones

    def _canonizar(self, metodo: str, ruta: str) -> str:
        for patron in self.lista_blanca:
            metodo_patron, _, ruta_patron = patron.partition(" ")
            if metodo_patron != metodo:
                continue
            expresion = "^" + re.escape(ruta_patron).replace(r"\{id\}", "[^/]+") + "$"
            if re.match(expresion, ruta):
                return patron
        return f"{metodo} {ruta}"


NEGRA_COMUN: tuple[tuple[str, str], ...] = (
    (r"\bInvoke-Expression\b|\biex\b", "invocacion dinamica de codigo"),
    (r"\bAdd-Type\b", "compilacion de codigo en tiempo de ejecucion"),
    (r"\[\s*Reflection\.", "acceso al modelo de reflexion"),
    (r"FromBase64String", "carga de contenido codificado"),
    (r"\bInvoke-WebRequest\b|\bInvoke-RestMethod\b|\bcurl\b|\bwget\b",
     "descarga desde internet"),
    (r"\bStart-Process\b|\bcmd\s*/c\b|\bpowershell\s+-enc", "ejecucion de procesos"),
    (r"\bRemove-ADObject\b|\bRemove-ADUser\b|\bRemove-ADGroup\b",
     "borrado de objetos del directorio"),
    (
        r"\bOut-File\b|\bSet-Content\b|>\s*[A-Za-z]:\\",
        "escritura en el sistema de archivos",
    ),
    (r"\brm\s+-rf\b|\bdel\s+/[sf]\b", "borrado masivo"),
    (r"\bSet-ExecutionPolicy\b", "cambio de la politica de ejecucion"),
)


POWERSHELL = PowerShell(
    codigo="powershell",
    nombre="Active Directory (PowerShell)",
    lista_blanca=frozenset(
        {
            "Add-ADGroupMember",
            "Remove-ADGroupMember",
            "Enable-ADAccount",
            "Disable-ADAccount",
            "Set-ADUser",
            "Get-ADUser",
            "Get-ADGroup",
            "Get-ADGroupMember",
            "Write-Output",
        }
    ),
    lista_negra=NEGRA_COMUN,
    extractores=(
        r"-Identity\s+['\"]?([A-Za-z0-9_\-\.]+)",
        r"-Members\s+['\"]?([A-Za-z0-9_\-\.]+)",
        r"-MemberOf\s+['\"]?([A-Za-z0-9_\-\.]+)",
    ),
)


OKTA = Okta(
    codigo="okta",
    nombre="Okta (API de administracion)",
    lista_blanca=frozenset(
        {
            "PUT /api/v1/groups/{id}/users/{id}",
            "DELETE /api/v1/groups/{id}/users/{id}",
            "POST /api/v1/users/{id}/lifecycle/activate",
            "POST /api/v1/users/{id}/lifecycle/deactivate",
            "GET /api/v1/users/{id}",
            "GET /api/v1/groups/{id}",
        }
    ),
    lista_negra=(
        (
            r'"metodo"\s*:\s*"DELETE"\s*,\s*"ruta"\s*:\s*"/api/v1/users/',
            "baja de usuarios",
        ),
        (r"/api/v1/apps", "administracion de aplicaciones"),
        (r"/api/v1/authorizationServers", "administracion del servidor de autorizacion"),
        (r"/api/v1/policies", "modificacion de politicas"),
        (r"/api/v1/trustedOrigins", "modificacion de origenes confiables"),
    ),
    extractores=(
        r'"(?:usuario|userId)"\s*:\s*"([^"]+)"',
        r'"(?:grupo|groupId)"\s*:\s*"([^"]+)"',
    ),
)


PLATAFORMAS: dict[str, Plataforma] = {
    POWERSHELL.codigo: POWERSHELL,
    OKTA.codigo: OKTA,
}


def obtener(codigo: str) -> Plataforma:
    plataforma = PLATAFORMAS.get(codigo.lower())
    if plataforma is None:
        raise KeyError(f"plataforma no soportada: {codigo}")
    return plataforma
