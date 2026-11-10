"""Cadena de validacion de la salida del modelo de lenguaje.

La premisa de todo este modulo es que el texto que devuelve el modelo no es
confiable hasta que se demuestre lo contrario. No porque el proveedor sea
sospechoso, sino porque la salida se parece mucho a un comando ejecutable y
basta con que una vez se parezca demasiado para que el error sea caro.

Son seis controles y se aplican en orden. Los cinco primeros son
automaticos y estan aca; el sexto es la aprobacion humana, que no es un
control tecnico sino una condicion del diseno y vive en el estado del
registro.

1. Esquema. La respuesta tiene que cumplir una estructura definida. Un
   desvio se rechaza sin mirar el contenido.
2. Sintaxis. Se analiza el script con el analizador de su lenguaje, sin
   ejecutarlo.
3. Lista blanca. Solo se aceptan operaciones de un conjunto cerrado.
4. Lista negra. Patrones prohibidos, aunque la lista blanca los dejara
   pasar.
5. Correspondencia. Cada identificador que aparece en el script tiene que
   estar en la decision que lo origino. Es el control mas especifico del
   dominio y el que ataca de frente la alucinacion: no evalua si la salida
   es plausible sino si coincide con una decision previa y verificable.

El orden importa. Los controles baratos van primero, y los que miran el
contenido solo corren sobre algo que ya tiene forma de script.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.modulos.plataformas import Plataforma, obtener

#: Tiempo maximo que se le da al analizador sintactico externo.
TIEMPO_LIMITE_PARSER = 15


class RespuestaModelo(BaseModel):
    """Estructura que el modelo tiene que devolver. Control 1."""

    lenguaje: str = Field(min_length=1, max_length=30)
    script: str = Field(min_length=1, max_length=20_000)
    explicacion: str = Field(default="", max_length=2_000)

    model_config = {"extra": "forbid"}


@dataclass
class ResultadoControl:
    nombre: str
    aprobado: bool
    detalle: str = ""
    hallazgos: list[str] = field(default_factory=list)


@dataclass
class ResultadoValidacion:
    """Lo que la cadena devuelve, con el detalle de cada control."""

    aprobado: bool
    controles: list[ResultadoControl] = field(default_factory=list)
    respuesta: RespuestaModelo | None = None

    @property
    def motivo(self) -> str:
        fallado = next((c for c in self.controles if not c.aprobado), None)
        return "" if fallado is None else f"{fallado.nombre}: {fallado.detalle}"

    def como_diccionario(self) -> dict[str, Any]:
        return {
            "aprobado": self.aprobado,
            "motivo": self.motivo,
            "controles": [
                {
                    "nombre": c.nombre,
                    "aprobado": c.aprobado,
                    "detalle": c.detalle,
                    "hallazgos": c.hallazgos,
                }
                for c in self.controles
            ],
        }


# ---------------------------------------------------------------------------
# Controles
# ---------------------------------------------------------------------------


def _control_esquema(bruto: str) -> tuple[ResultadoControl, RespuestaModelo | None]:
    """Control 1: la respuesta cumple el esquema definido."""
    texto = _limpiar_cerca_de_json(bruto)
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as error:
        return (
            ResultadoControl("esquema", False, f"la respuesta no es JSON: {error.msg}"),
            None,
        )
    if not isinstance(datos, dict):
        return ResultadoControl("esquema", False, "la raiz no es un objeto"), None
    try:
        respuesta = RespuestaModelo.model_validate(datos)
    except ValidationError as error:
        campos = ", ".join(".".join(map(str, e["loc"])) for e in error.errors())
        return (
            ResultadoControl("esquema", False, f"campos invalidos: {campos}"),
            None,
        )
    return ResultadoControl("esquema", True, "estructura correcta"), respuesta


def _limpiar_cerca_de_json(bruto: str) -> str:
    """Quita el envoltorio de markdown que algunos modelos agregan igual.

    Es una concesion pequena y acotada: se retira el cercado de bloque de
    codigo, nada mas. No se intenta reparar JSON mal formado, porque un
    modelo que no puede devolver JSON valido no deberia ser compensado por
    el validador.
    """
    texto = bruto.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```[a-zA-Z]*\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)
    return texto.strip()


def _control_sintaxis(
    plataforma: Plataforma, script: str
) -> ResultadoControl:
    """Control 2: el script parsea en su propio lenguaje, sin ejecutarse."""
    if plataforma.codigo == "powershell":
        return _sintaxis_powershell(script)
    if plataforma.codigo == "okta":
        return _sintaxis_okta(script)
    return ResultadoControl("sintaxis", False, "plataforma sin analizador")


def _sintaxis_powershell(script: str) -> ResultadoControl:
    """Usa el analizador de PowerShell cuando esta disponible.

    El parser de PowerShell analiza sin ejecutar, que es exactamente lo que
    hace falta. Cuando el binario no esta en el entorno —el contenedor de
    integracion continua, por ejemplo— se cae a una verificacion estructural
    propia, mas debil. El resultado dice cual de las dos corrio, porque un
    control que no se sabe cual fue no sirve como evidencia.
    """
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh is None:
        return _sintaxis_estructural(script)

    comando = (
        "$errores = $null; "
        "[System.Management.Automation.Language.Parser]::ParseInput("
        "[Console]::In.ReadToEnd(), [ref]$null, [ref]$errores) | Out-Null; "
        "if ($errores.Count -gt 0) { "
        "$errores | ForEach-Object { $_.Message }; exit 1 } else { exit 0 }"
    )
    try:
        proceso = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", comando],
            input=script,
            capture_output=True,
            text=True,
            timeout=TIEMPO_LIMITE_PARSER,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        return ResultadoControl(
            "sintaxis", False, f"el analizador no pudo ejecutarse: {error}"
        )

    if proceso.returncode == 0:
        return ResultadoControl("sintaxis", True, "analizador de PowerShell: sin errores")
    errores = [linea for linea in proceso.stdout.splitlines() if linea.strip()]
    return ResultadoControl(
        "sintaxis", False, "errores de parseo", hallazgos=errores[:10]
    )


def _sintaxis_estructural(script: str) -> ResultadoControl:
    """Verificacion de respaldo: estructura balanceada y sin cadenas abiertas."""
    problemas = []
    for apertura, cierre, nombre in (("(", ")", "parentesis"), ("{", "}", "llaves")):
        if script.count(apertura) != script.count(cierre):
            problemas.append(f"{nombre} sin balancear")
    for comilla, nombre in (("'", "comilla simple"), ('"', "comilla doble")):
        if script.count(comilla) % 2 != 0:
            problemas.append(f"{nombre} sin cerrar")
    if not script.strip():
        problemas.append("script vacio")

    if problemas:
        return ResultadoControl(
            "sintaxis", False, "verificacion estructural de respaldo", hallazgos=problemas
        )
    return ResultadoControl(
        "sintaxis",
        True,
        "verificacion estructural de respaldo: sin errores "
        "(PowerShell no disponible en este entorno)",
    )


def _sintaxis_okta(script: str) -> ResultadoControl:
    """El cuerpo tiene que ser una lista de llamadas bien formadas."""
    try:
        datos = json.loads(script)
    except json.JSONDecodeError as error:
        return ResultadoControl("sintaxis", False, f"JSON invalido: {error.msg}")

    llamadas = [datos] if isinstance(datos, dict) else datos
    if not isinstance(llamadas, list) or not llamadas:
        return ResultadoControl("sintaxis", False, "se esperaba una lista de llamadas")

    problemas = []
    for indice, llamada in enumerate(llamadas):
        if not isinstance(llamada, dict):
            problemas.append(f"llamada {indice}: no es un objeto")
            continue
        metodo = llamada.get("metodo", llamada.get("method"))
        ruta = llamada.get("ruta", llamada.get("path"))
        if metodo not in ("GET", "POST", "PUT", "DELETE"):
            problemas.append(f"llamada {indice}: metodo invalido ({metodo})")
        if not isinstance(ruta, str) or not ruta.startswith("/api/v1/"):
            problemas.append(f"llamada {indice}: ruta invalida ({ruta})")

    if problemas:
        return ResultadoControl(
            "sintaxis", False, "llamadas mal formadas", hallazgos=problemas[:10]
        )
    return ResultadoControl("sintaxis", True, f"{len(llamadas)} llamadas bien formadas")


def _control_lista_blanca(plataforma: Plataforma, script: str) -> ResultadoControl:
    """Control 3: solo operaciones del conjunto cerrado."""
    operaciones = plataforma.operaciones(script)
    if not operaciones:
        return ResultadoControl(
            "lista_blanca", False, "no se reconocio ninguna operacion en el script"
        )
    fuera = sorted({o for o in operaciones if o not in plataforma.lista_blanca})
    if fuera:
        return ResultadoControl(
            "lista_blanca",
            False,
            "operaciones fuera de la lista blanca",
            hallazgos=fuera,
        )
    return ResultadoControl(
        "lista_blanca", True, f"{len(operaciones)} operaciones, todas permitidas"
    )


def _control_lista_negra(plataforma: Plataforma, script: str) -> ResultadoControl:
    """Control 4: patrones prohibidos, aunque la lista blanca los permitiera."""
    encontrados = [
        motivo
        for patron, motivo in plataforma.lista_negra
        if re.search(patron, script, re.IGNORECASE)
    ]
    if encontrados:
        return ResultadoControl(
            "lista_negra",
            False,
            "el script contiene patrones prohibidos",
            hallazgos=sorted(set(encontrados)),
        )
    return ResultadoControl("lista_negra", True, "sin patrones prohibidos")


def _control_correspondencia(
    plataforma: Plataforma, script: str, autorizados: set[str]
) -> ResultadoControl:
    """Control 5: todo identificador del script esta en la decision previa.

    Es el control que distingue a este sistema de uno que simplemente le
    pide scripts a un modelo. No pregunta si la salida tiene sentido: pregunta
    si coincide con algo que el motor de reglas ya decidio. Un grupo
    inventado, o uno real que no correspondia a esta decision, cae aca.
    """
    presentes = plataforma.identificadores(script)
    if not presentes:
        return ResultadoControl(
            "correspondencia",
            False,
            "no se encontro ningun identificador para contrastar",
        )
    normalizados = {x.casefold() for x in autorizados}
    inventados = sorted(x for x in presentes if x.casefold() not in normalizados)
    if inventados:
        return ResultadoControl(
            "correspondencia",
            False,
            "el script menciona identificadores que no estan en la decision",
            hallazgos=inventados,
        )
    return ResultadoControl(
        "correspondencia",
        True,
        f"{len(presentes)} identificadores, todos autorizados",
    )


# ---------------------------------------------------------------------------
# Cadena
# ---------------------------------------------------------------------------


def validar_salida(
    bruto: str, plataforma_codigo: str, autorizados: set[str]
) -> ResultadoValidacion:
    """Aplica los cinco controles automaticos en orden.

    La cadena corta en el primer fallo. No tiene sentido buscar patrones
    prohibidos en algo que ni siquiera parsea, y seguir produciria una lista
    de errores derivados que confunde mas de lo que ayuda.
    """
    resultado = ResultadoValidacion(aprobado=False)

    control, respuesta = _control_esquema(bruto)
    resultado.controles.append(control)
    if not control.aprobado or respuesta is None:
        return resultado
    resultado.respuesta = respuesta

    try:
        plataforma = obtener(plataforma_codigo)
    except KeyError as error:
        resultado.controles.append(ResultadoControl("sintaxis", False, str(error)))
        return resultado

    if respuesta.lenguaje.lower() != plataforma.codigo:
        resultado.controles.append(
            ResultadoControl(
                "esquema",
                False,
                f"el modelo declaro lenguaje '{respuesta.lenguaje}' y se pidio "
                f"'{plataforma.codigo}'",
            )
        )
        return resultado

    for control in (
        _control_sintaxis(plataforma, respuesta.script),
        _control_lista_blanca(plataforma, respuesta.script),
        _control_lista_negra(plataforma, respuesta.script),
        _control_correspondencia(plataforma, respuesta.script, autorizados),
    ):
        resultado.controles.append(control)
        if not control.aprobado:
            return resultado

    resultado.aprobado = True
    return resultado
