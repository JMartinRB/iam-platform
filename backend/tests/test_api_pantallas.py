"""Pruebas de los endpoints que consumen las pantallas.

Ejercitan el recorrido completo contra la API y no contra los módulos: es
el único camino que prueba lo que el navegador va a hacer realmente, con
sus serializaciones, sus códigos de estado y sus dependencias.

Dos de estas pruebas no verifican que algo funcione sino que algo *no*
pueda hacerse: aprobar la propia solicitud y aprobar un script que no pasó
la cadena de validación. Son las que impiden que una refactorización
futura deje esas puertas abiertas sin que nadie se entere.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.nucleo.base_datos import obtener_fabrica_sesiones, obtener_motor
from app.nucleo.config import obtener_config

PERFIL = "D-1"


@pytest.fixture()
def cliente(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """Cliente contra una base SQLite propia de cada prueba.

    Hay que limpiar las tres cachés: la configuración, el motor y la
    fábrica de sesiones son `lru_cache`, así que sin esto la segunda
    prueba seguiría escribiendo en la base de la primera y los conteos
    no significarían nada.
    """
    monkeypatch.setitem(
        os.environ, "DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'api.db'}"
    )
    for cache in (obtener_config, obtener_motor, obtener_fabrica_sesiones):
        cache.cache_clear()

    from app.main import app

    with TestClient(app) as c:
        yield c

    for cache in (obtener_config, obtener_motor, obtener_fabrica_sesiones):
        cache.cache_clear()


@pytest.fixture()
def con_datos(cliente: TestClient) -> tuple[TestClient, int]:
    """Cliente con un snapshot sintético ya cargado y analizado."""
    carga = cliente.post("/api/snapshots/generar", json={"perfil": PERFIL})
    assert carga.status_code == 201, carga.text
    snapshot_id = carga.json()["snapshot_id"]
    assert cliente.post(f"/api/snapshots/{snapshot_id}/analizar").status_code == 200
    return cliente, snapshot_id


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


def test_el_esquema_se_crea_al_arrancar(cliente: TestClient) -> None:
    """Abrir la aplicación recién instalada no puede dar error de tabla."""
    respuesta = cliente.get("/api/snapshots")
    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_la_carga_informa_cada_fila_rechazada(cliente: TestClient) -> None:
    """El conjunto trae defectos a propósito y ninguno debe pasar callado."""
    cuerpo = cliente.post("/api/snapshots/generar", json={"perfil": PERFIL}).json()

    assert cuerpo["leidas"] > cuerpo["aceptadas"]
    assert cuerpo["rechazadas"] == len(cuerpo["rechazos"])
    assert cuerpo["rechazadas"] > 0
    for rechazo in cuerpo["rechazos"]:
        assert rechazo["archivo"]
        assert rechazo["linea"] > 0
        assert rechazo["motivo"]


def test_un_perfil_inexistente_se_rechaza(cliente: TestClient) -> None:
    respuesta = cliente.post("/api/snapshots/generar", json={"perfil": "D-99"})
    assert respuesta.status_code == 400


def test_importar_sin_los_archivos_del_esquema_falla_diciendo_cuales(
    cliente: TestClient, tmp_path
) -> None:
    incompleto = tmp_path / "usuarios.csv"
    incompleto.write_text("legajo,nombre\n", encoding="utf-8")

    with incompleto.open("rb") as f:
        respuesta = cliente.post(
            "/api/snapshots/importar", files={"archivos": ("usuarios.csv", f, "text/csv")}
        )

    assert respuesta.status_code == 422
    assert "permisos.csv" in respuesta.json()["detail"]


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------


def test_el_panel_devuelve_las_cinco_categorias(con_datos) -> None:
    cliente, snapshot_id = con_datos
    panel = cliente.get(f"/api/snapshots/{snapshot_id}/resumen").json()

    categorias = {c["categoria"] for c in panel["categorias"]}
    assert categorias == {
        "cuenta_dormida",
        "cuenta_huerfana",
        "sobre_privilegio",
        "conflicto_sod",
        "privilege_creep",
    }
    assert panel["analizado"] is True
    assert panel["hallazgos"] == sum(c["hallazgos"] for c in panel["categorias"])


def test_el_indice_de_riesgo_esta_acotado_y_es_coherente(con_datos) -> None:
    cliente, snapshot_id = con_datos
    panel = cliente.get(f"/api/snapshots/{snapshot_id}/resumen").json()

    assert 0 <= panel["indice_riesgo"] <= 100
    assert panel["nivel_riesgo"] in {"bajo", "medio", "alto", "critico"}
    assert panel["usuarios_senalados"] <= panel["usuarios_analizados"]


def test_reanalizar_no_duplica_los_hallazgos(con_datos) -> None:
    """Analizar dos veces el mismo snapshot tiene que dar lo mismo.

    Sin el borrado previo, cada corrida sumaba sus hallazgos a los
    anteriores y el panel contaba dos veces cada caso.
    """
    cliente, snapshot_id = con_datos
    primero = cliente.get(f"/api/snapshots/{snapshot_id}/resumen").json()
    segundo = cliente.post(f"/api/snapshots/{snapshot_id}/analizar").json()

    assert segundo["hallazgos"] == primero["hallazgos"]
    assert segundo["indice_riesgo"] == primero["indice_riesgo"]


def test_el_filtro_por_severidad_devuelve_solo_esa_severidad(con_datos) -> None:
    cliente, snapshot_id = con_datos
    criticos = cliente.get(
        f"/api/snapshots/{snapshot_id}/hallazgos", params={"severidad": "critica"}
    ).json()

    assert criticos
    assert all(h["severidad"] == "critica" for h in criticos)


def test_el_detalle_trae_los_cinco_elementos_que_pide_rf10(con_datos) -> None:
    cliente, snapshot_id = con_datos
    hallazgo = cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()[0]

    detalle = cliente.get(
        f"/api/snapshots/{snapshot_id}/hallazgos/{hallazgo['id']}"
    ).json()

    assert detalle["regla_aplicada"]
    assert detalle["umbral"]
    assert detalle["fuente"]
    assert detalle["fecha_snapshot"]
    assert detalle["contexto"]


def test_el_umbral_del_detalle_sale_de_los_parametros_de_la_corrida(con_datos) -> None:
    """El texto no puede estar escrito a mano: tiene que decir el valor real."""
    cliente, snapshot_id = con_datos
    dormidas = cliente.get(
        f"/api/snapshots/{snapshot_id}/hallazgos", params={"categoria": "cuenta_dormida"}
    ).json()

    detalle = cliente.get(
        f"/api/snapshots/{snapshot_id}/hallazgos/{dormidas[0]['id']}"
    ).json()

    assert str(obtener_config().dias_cuenta_dormida) in detalle["umbral"]


def test_un_snapshot_inexistente_da_404(cliente: TestClient) -> None:
    assert cliente.get("/api/snapshots/999/resumen").status_code == 404


# ---------------------------------------------------------------------------
# Altas
# ---------------------------------------------------------------------------


def _primer_usuario(cliente: TestClient) -> str:
    return cliente.get("/api/usuarios", params={"limite": 1}).json()[0]["legajo"]


def test_el_alta_resuelve_los_accesos_con_el_motor_de_reglas(con_datos) -> None:
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)

    respuesta = cliente.post(
        "/api/altas",
        json={
            "legajo": legajo,
            "conservar_vigentes": False,
            "solicitada_por": "U00002",
        },
    )

    assert respuesta.status_code == 201
    alta = respuesta.json()
    assert alta["otorgados"]
    assert all(p["origen"] for p in alta["otorgados"])
    assert alta["estado"] == "pendiente"


def test_un_legajo_inexistente_da_404(con_datos) -> None:
    cliente, _ = con_datos
    respuesta = cliente.post(
        "/api/altas", json={"legajo": "NO-EXISTE", "solicitada_por": "U00002"}
    )
    assert respuesta.status_code == 404


def test_un_permiso_pedido_a_mano_pasa_igual_por_la_validacion_sod(con_datos) -> None:
    """Pedir un acceso explícitamente no puede saltear el control cruzado."""
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)
    regla = _regla_critica(cliente)

    alta = cliente.post(
        "/api/altas",
        json={
            "legajo": legajo,
            "conservar_vigentes": False,
            "permisos_solicitados": [regla["a"], regla["b"]],
            "solicitada_por": "U00002",
        },
    ).json()

    conflictos = [e for e in alta["excluidos"] if e["motivo"] == "conflicto_sod"]
    assert conflictos, "el conflicto tenía que detectarse"
    assert conflictos[0]["regla"]
    assert conflictos[0]["permiso_en_conflicto"]
    otorgados = {p["codigo"] for p in alta["otorgados"]}
    assert conflictos[0]["codigo"] not in otorgados


def _regla_critica(cliente: TestClient) -> dict[str, str]:
    """Dos permisos que la matriz declara incompatibles entre sí."""
    from sqlalchemy import select

    from app.nucleo.modelos import Permiso, ReglaSoD

    with obtener_fabrica_sesiones()() as s:
        permisos = {p.id: p.codigo for p in s.scalars(select(Permiso)).all()}
        regla = s.scalars(
            select(ReglaSoD).where(ReglaSoD.severidad == "critica").limit(1)
        ).one()
        return {"a": permisos[regla.permiso_a_id], "b": permisos[regla.permiso_b_id]}


def test_el_permiso_bloqueado_no_entra_al_script(con_datos) -> None:
    """El script sólo puede contener lo que el motor efectivamente otorgó."""
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)
    regla = _regla_critica(cliente)

    alta = cliente.post(
        "/api/altas",
        json={
            "legajo": legajo,
            "conservar_vigentes": False,
            "permisos_solicitados": [regla["a"], regla["b"]],
            "solicitada_por": "U00002",
        },
    ).json()

    script = cliente.post(
        f"/api/altas/{alta['id']}/script",
        json={"plataforma": "powershell", "actor": "U00002"},
    ).json()

    excluido = next(e["codigo"] for e in alta["excluidos"])
    assert excluido not in script["contenido"]


def test_el_script_no_lleva_el_legajo_real(con_datos) -> None:
    """Lo que viaja al modelo y queda guardado es un seudónimo."""
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)

    alta = cliente.post(
        "/api/altas",
        json={
            "legajo": legajo,
            "conservar_vigentes": False,
            "solicitada_por": "U00002",
        },
    ).json()
    script = cliente.post(
        f"/api/altas/{alta['id']}/script",
        json={"plataforma": "powershell", "actor": "U00002"},
    ).json()

    assert legajo not in script["contenido"]


def test_el_script_declara_si_lo_produjo_un_modelo(con_datos) -> None:
    """Sin esta marca, la pantalla podría presentar como salida de un
    modelo algo que armó el proveedor simulado."""
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)

    alta = cliente.post(
        "/api/altas",
        json={"legajo": legajo, "conservar_vigentes": False, "solicitada_por": "U00002"},
    ).json()
    script = cliente.post(
        f"/api/altas/{alta['id']}/script",
        json={"plataforma": "powershell", "actor": "U00002", "usar_modelo_real": False},
    ).json()

    assert script["proveedor_es_simulado"] is True
    assert script["estado_validacion"] == "valido"
    assert [c["nombre"] for c in script["controles"]]
    assert all(c["aprobado"] for c in script["controles"])


def test_sin_cambios_que_aplicar_no_se_genera_script(con_datos) -> None:
    """Quien ya tiene todo lo que le corresponde no genera ningún cambio."""
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)

    alta = cliente.post(
        "/api/altas",
        json={"legajo": legajo, "conservar_vigentes": True, "solicitada_por": "U00002"},
    ).json()

    if alta["cambios_pendientes"] == 0:
        respuesta = cliente.post(
            f"/api/altas/{alta['id']}/script",
            json={"plataforma": "powershell", "actor": "U00002"},
        )
        assert respuesta.status_code == 422


# ---------------------------------------------------------------------------
# Aprobación: lo que el sistema tiene que impedir
# ---------------------------------------------------------------------------


def test_nadie_puede_aprobar_su_propia_solicitud(con_datos) -> None:
    """La misma separación de funciones que el sistema audita, aplicada al
    propio sistema."""
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)

    alta = cliente.post(
        "/api/altas",
        json={"legajo": legajo, "conservar_vigentes": False, "solicitada_por": "U00002"},
    ).json()

    propia = cliente.post(
        f"/api/altas/{alta['id']}/resolver", json={"actor": "U00002", "aprobar": True}
    )
    assert propia.status_code == 403

    ajena = cliente.post(
        f"/api/altas/{alta['id']}/resolver", json={"actor": "U00009", "aprobar": True}
    )
    assert ajena.status_code == 200
    assert ajena.json()["estado"] == "aprobada"


def test_una_solicitud_ya_resuelta_no_se_resuelve_de_nuevo(con_datos) -> None:
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)
    alta = cliente.post(
        "/api/altas",
        json={"legajo": legajo, "conservar_vigentes": False, "solicitada_por": "U00002"},
    ).json()

    cliente.post(
        f"/api/altas/{alta['id']}/resolver", json={"actor": "U00009", "aprobar": True}
    )
    repetida = cliente.post(
        f"/api/altas/{alta['id']}/resolver", json={"actor": "U00009", "aprobar": True}
    )
    assert repetida.status_code == 409


def test_un_script_rechazado_por_la_cadena_no_se_puede_aprobar_a_mano(
    con_datos, monkeypatch
) -> None:
    """Si la máquina dijo que la salida no sirve, un clic no puede
    habilitarla: eso volvería decorativos los controles."""
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)

    alta = cliente.post(
        "/api/altas",
        json={"legajo": legajo, "conservar_vigentes": False, "solicitada_por": "U00002"},
    ).json()
    script = cliente.post(
        f"/api/altas/{alta['id']}/script",
        json={"plataforma": "powershell", "actor": "U00002"},
    ).json()

    from app.nucleo.enums import EstadoValidacion
    from app.nucleo.modelos import ScriptGenerado

    with obtener_fabrica_sesiones()() as s:
        guardado = s.get(ScriptGenerado, script["id"])
        guardado.estado_validacion = EstadoValidacion.RECHAZADO
        s.commit()

    respuesta = cliente.post(
        f"/api/scripts/{script['id']}/aprobar", json={"actor": "U00009", "aprobar": True}
    )
    assert respuesta.status_code == 409


def test_un_script_no_se_aprueba_dos_veces(con_datos) -> None:
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)
    alta = cliente.post(
        "/api/altas",
        json={"legajo": legajo, "conservar_vigentes": False, "solicitada_por": "U00002"},
    ).json()
    script = cliente.post(
        f"/api/altas/{alta['id']}/script",
        json={"plataforma": "powershell", "actor": "U00002"},
    ).json()

    primera = cliente.post(
        f"/api/scripts/{script['id']}/aprobar", json={"actor": "U00009", "aprobar": True}
    )
    assert primera.status_code == 200

    segunda = cliente.post(
        f"/api/scripts/{script['id']}/aprobar", json={"actor": "U00010", "aprobar": True}
    )
    assert segunda.status_code == 409


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------


def test_la_auditoria_registra_todo_el_recorrido(con_datos) -> None:
    cliente, _ = con_datos
    legajo = _primer_usuario(cliente)

    alta = cliente.post(
        "/api/altas",
        json={"legajo": legajo, "conservar_vigentes": False, "solicitada_por": "U00002"},
    ).json()
    script = cliente.post(
        f"/api/altas/{alta['id']}/script",
        json={"plataforma": "powershell", "actor": "U00002"},
    ).json()
    cliente.post(
        f"/api/scripts/{script['id']}/aprobar", json={"actor": "U00009", "aprobar": True}
    )
    cliente.post(
        f"/api/altas/{alta['id']}/resolver", json={"actor": "U00009", "aprobar": True}
    )

    acciones = {e["accion"] for e in cliente.get("/api/auditoria").json()}
    assert {
        "importar_dataset",
        "analizar_snapshot",
        "registrar_solicitud",
        "generar_script",
        "aprobar_script",
        "resolver_solicitud",
    } <= acciones


def test_la_auditoria_no_expone_escritura(cliente: TestClient) -> None:
    """El registro es de sólo lectura: no hay forma de escribirlo ni
    borrarlo desde la API."""
    from app.main import app

    rutas = [
        r for r in app.routes
        if getattr(r, "path", "").startswith("/api/auditoria")
    ]
    assert rutas
    for ruta in rutas:
        assert set(ruta.methods) <= {"GET", "HEAD"}


# ---------------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------------


def test_la_interfaz_se_sirve_desde_la_raiz(cliente: TestClient) -> None:
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    assert "SAGA" in respuesta.text


def test_los_archivos_de_la_interfaz_se_sirven(cliente: TestClient) -> None:
    for ruta in ("/css/estilo.css", "/js/app.js", "/js/panel.js", "/js/altas.js"):
        assert cliente.get(ruta).status_code == 200, ruta


# ---------------------------------------------------------------------------
# Excepciones justificadas (RF-11)
# ---------------------------------------------------------------------------


def test_un_hallazgo_exceptuado_sale_del_panel_y_del_indice(con_datos) -> None:
    cliente, snapshot_id = con_datos
    antes = cliente.get(f"/api/snapshots/{snapshot_id}/resumen").json()
    hallazgo = cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()[0]

    respuesta = cliente.post(
        f"/api/snapshots/{snapshot_id}/hallazgos/{hallazgo['id']}/excepcion",
        json={
            "motivo": "acceso aprobado por el dueño del proceso",
            "responsable": "U00009",
            "solo_esta_regla": False,
        },
    )
    assert respuesta.status_code == 201
    assert respuesta.json()["exceptuado"] is True

    despues = cliente.get(f"/api/snapshots/{snapshot_id}/resumen").json()
    assert despues["exceptuados"] > 0
    assert despues["hallazgos"] < antes["hallazgos"]
    assert despues["indice_riesgo"] <= antes["indice_riesgo"]

    visibles = {
        h["id"] for h in cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()
    }
    assert hallazgo["id"] not in visibles


def test_el_hallazgo_exceptuado_se_puede_listar_igual(con_datos) -> None:
    """Exceptuar no es esconder: con el filtro correspondiente aparece."""
    cliente, snapshot_id = con_datos
    hallazgo = cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()[0]
    cliente.post(
        f"/api/snapshots/{snapshot_id}/hallazgos/{hallazgo['id']}/excepcion",
        json={"motivo": "justificado", "responsable": "U00009"},
    )

    con_todo = cliente.get(
        f"/api/snapshots/{snapshot_id}/hallazgos",
        params={"incluir_exceptuados": True},
    ).json()

    encontrado = next(h for h in con_todo if h["id"] == hallazgo["id"])
    assert encontrado["exceptuado"] is True
    assert encontrado["excepcion"]["motivo"] == "justificado"
    assert encontrado["excepcion"]["responsable"] == "U00009"


def test_una_excepcion_sin_motivo_se_rechaza_en_la_api(con_datos) -> None:
    cliente, snapshot_id = con_datos
    hallazgo = cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()[0]

    respuesta = cliente.post(
        f"/api/snapshots/{snapshot_id}/hallazgos/{hallazgo['id']}/excepcion",
        json={"motivo": "", "responsable": "U00009"},
    )
    assert respuesta.status_code == 422


def test_revocar_devuelve_el_hallazgo_al_panel(con_datos) -> None:
    cliente, snapshot_id = con_datos
    antes = cliente.get(f"/api/snapshots/{snapshot_id}/resumen").json()["hallazgos"]
    hallazgo = cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()[0]
    cliente.post(
        f"/api/snapshots/{snapshot_id}/hallazgos/{hallazgo['id']}/excepcion",
        json={"motivo": "justificado", "responsable": "U00009"},
    )

    excepcion = cliente.get(f"/api/snapshots/{snapshot_id}/excepciones").json()[0]
    revocada = cliente.request(
        "DELETE",
        f"/api/snapshots/{snapshot_id}/excepciones/{excepcion['id']}",
        params={"actor": "U00010"},
    )
    assert revocada.status_code == 200

    despues = cliente.get(f"/api/snapshots/{snapshot_id}/resumen").json()
    assert despues["hallazgos"] == antes
    assert despues["exceptuados"] == 0


def test_la_excepcion_sobrevive_a_un_nuevo_diagnostico(con_datos) -> None:
    """Es el criterio de aceptación de RF-11, palabra por palabra: un
    hallazgo marcado como excepción no vuelve a aparecer como riesgo en el
    diagnóstico siguiente."""
    cliente, snapshot_id = con_datos
    hallazgo = cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()[0]
    legajo = hallazgo["legajo"]
    categoria = hallazgo["categoria"]

    cliente.post(
        f"/api/snapshots/{snapshot_id}/hallazgos/{hallazgo['id']}/excepcion",
        json={
            "motivo": "justificado por el negocio",
            "responsable": "U00009",
            "solo_esta_regla": False,
        },
    )

    # Se vuelve a analizar: los hallazgos se borran y se emiten de nuevo,
    # con identificadores distintos.
    cliente.post(f"/api/snapshots/{snapshot_id}/analizar")

    visibles = cliente.get(
        f"/api/snapshots/{snapshot_id}/hallazgos",
        params={"categoria": categoria, "limite": 500},
    ).json()
    assert all(h["legajo"] != legajo for h in visibles)

    con_todo = cliente.get(
        f"/api/snapshots/{snapshot_id}/hallazgos",
        params={"categoria": categoria, "incluir_exceptuados": True, "limite": 500},
    ).json()
    assert any(h["legajo"] == legajo and h["exceptuado"] for h in con_todo)


def test_la_excepcion_queda_en_la_auditoria(con_datos) -> None:
    cliente, snapshot_id = con_datos
    hallazgo = cliente.get(f"/api/snapshots/{snapshot_id}/hallazgos").json()[0]
    cliente.post(
        f"/api/snapshots/{snapshot_id}/hallazgos/{hallazgo['id']}/excepcion",
        json={"motivo": "justificado", "responsable": "U00009"},
    )

    eventos = cliente.get(
        "/api/auditoria", params={"entidad": "excepcion_hallazgo"}
    ).json()
    assert eventos
    assert eventos[0]["accion"] == "registrar_excepcion"
    assert eventos[0]["detalle"]["motivo"] == "justificado"
