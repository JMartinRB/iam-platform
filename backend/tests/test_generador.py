"""Verificacion del generador de datasets sinteticos.

Estas pruebas no comprueban que el generador corra: comprueban que la
verdad de referencia que declara en ``manifiesto.json`` sea exactamente la
que se puede reconstruir leyendo los CSV. Sin esa garantia, la medicion de
precision y exhaustividad del indicador I-02 no significaria nada, porque
estaria contrastando los hallazgos del sistema contra una referencia que no
describe los datos.

El procedimiento es siempre el mismo: se recalcula cada categoria aplicando
su definicion sobre los archivos y se exige igualdad de conjuntos contra lo
plantado, no solo de cantidades. Un conjunto del mismo tamano pero con otros
legajos adentro seria un error que una comparacion de totales no veria.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import median

import pytest

from app.nucleo.enums import OrigenAsignacion
from app.nucleo.texto import normalizar_codigo
from herramientas.generar_dataset import DIAS_VENTANA_USO, PERFILES, generar
from tests.conftest import FECHA_CORTE

# ---------------------------------------------------------------------------
# Auxiliares: reconstruyen el estado del dataset desde los archivos
# ---------------------------------------------------------------------------


def _fecha(valor: str) -> date | None:
    try:
        return date.fromisoformat(valor)
    except (ValueError, TypeError):
        return None


def _validas(tablas: dict) -> list[dict]:
    """Filas de asignaciones que un importador correcto deberia aceptar."""
    legajos = {u["legajo"] for u in tablas["usuarios"]}
    permisos = {p["codigo"] for p in tablas["permisos"]}
    origenes = {o.value for o in OrigenAsignacion}
    aceptadas = []
    for fila in tablas["asignaciones"]:
        if fila["legajo"] not in legajos:
            continue
        if normalizar_codigo(fila["permiso_codigo"]) not in permisos:
            continue
        if fila["origen"] not in origenes:
            continue
        if _fecha(fila["fecha_otorgamiento"]) is None:
            continue
        aceptadas.append(fila)
    return aceptadas


def _activas_por_legajo(tablas: dict) -> dict[str, set[str]]:
    resultado: dict[str, set[str]] = defaultdict(set)
    for fila in _validas(tablas):
        if fila["activa"].lower() in ("true", "1", "si"):
            resultado[fila["legajo"]].add(normalizar_codigo(fila["permiso_codigo"]))
    return resultado


def _plantados(manifiesto: dict, categoria: str) -> set[str]:
    return {
        caso["legajo"]
        for caso in manifiesto["hallazgos_plantados"][categoria]["casos"]
    }


# ---------------------------------------------------------------------------
# Volumen y coherencia general
# ---------------------------------------------------------------------------


def test_volumen_coincide_con_el_perfil(dataset_d1: dict) -> None:
    volumen = dataset_d1["manifiesto"]["volumen"]
    perfil = PERFILES["D-1"]
    assert volumen["asignaciones"] == perfil.asignaciones
    # los usuarios declarados incluyen los duplicados que se agregan como ruido
    assert volumen["usuarios"] == perfil.usuarios + dataset_d1["manifiesto"]["ruido"][
        "usuarios_duplicados"
    ]
    assert volumen["aplicaciones"] == perfil.aplicaciones


def test_el_manifiesto_describe_las_filas_del_csv(dataset_d1: dict) -> None:
    tablas = dataset_d1["tablas"]
    volumen = dataset_d1["manifiesto"]["volumen"]
    assert len(tablas["asignaciones"]) == volumen["filas_totales_asignaciones"]
    assert len(tablas["usuarios"]) == volumen["usuarios"]
    assert len(tablas["permisos"]) == volumen["permisos"]
    assert len(tablas["reglas_sod"]) == volumen["reglas_sod"]


def test_integridad_referencial_de_las_filas_validas(dataset_d1: dict) -> None:
    tablas = dataset_d1["tablas"]
    permisos = {p["codigo"]: p for p in tablas["permisos"]}
    apps = {a["codigo"] for a in tablas["aplicaciones"]}
    for fila in _validas(tablas):
        permiso = permisos[normalizar_codigo(fila["permiso_codigo"])]
        assert permiso["aplicacion_codigo"] in apps
        if fila["origen"] == OrigenAsignacion.RBAC_ROL:
            assert fila["rol_codigo"], "una asignacion por rol debe indicar el rol"


def test_las_categorias_plantadas_no_se_solapan(dataset_d1: dict) -> None:
    manifiesto = dataset_d1["manifiesto"]
    vistos: dict[str, str] = {}
    for categoria in manifiesto["hallazgos_plantados"]:
        for legajo in _plantados(manifiesto, categoria):
            assert legajo not in vistos, (
                f"{legajo} figura en {vistos.get(legajo)} y en {categoria}"
            )
            vistos[legajo] = categoria


# ---------------------------------------------------------------------------
# Una prueba por categoria: el conjunto recalculado tiene que ser el plantado
# ---------------------------------------------------------------------------


def test_cuentas_dormidas_son_exactamente_las_plantadas(dataset_d1: dict) -> None:
    detectadas = {
        u["legajo"]
        for u in dataset_d1["tablas"]["usuarios"]
        if u["estado"] == "activo"
        and _fecha(u["ultimo_acceso"]) is not None
        and (FECHA_CORTE - _fecha(u["ultimo_acceso"])).days > DIAS_VENTANA_USO
    }
    assert detectadas == _plantados(dataset_d1["manifiesto"], "cuenta_dormida")


def test_cuentas_huerfanas_son_exactamente_las_plantadas(dataset_d1: dict) -> None:
    activas = _activas_por_legajo(dataset_d1["tablas"])
    detectadas = {
        u["legajo"]
        for u in dataset_d1["tablas"]["usuarios"]
        if u["estado"] == "baja" and activas.get(u["legajo"])
    }
    assert detectadas == _plantados(dataset_d1["manifiesto"], "cuenta_huerfana")


def test_conflictos_sod_son_exactamente_los_plantados(dataset_d1: dict) -> None:
    reglas = [
        (r["codigo"], r["permiso_a"], r["permiso_b"])
        for r in dataset_d1["tablas"]["reglas_sod"]
        if r["activa"].lower() == "true"
    ]
    activas = _activas_por_legajo(dataset_d1["tablas"])
    detectados = {
        legajo
        for legajo, permisos in activas.items()
        for _, a, b in reglas
        if a in permisos and b in permisos
    }
    assert detectados == _plantados(dataset_d1["manifiesto"], "conflicto_sod")


def test_cada_conflicto_plantado_coincide_con_su_regla(dataset_d1: dict) -> None:
    reglas = {r["codigo"]: r for r in dataset_d1["tablas"]["reglas_sod"]}
    activas = _activas_por_legajo(dataset_d1["tablas"])
    casos = dataset_d1["manifiesto"]["hallazgos_plantados"]["conflicto_sod"]["casos"]
    for caso in casos:
        regla = reglas[caso["regla"]]
        permisos = activas[caso["legajo"]]
        assert regla["permiso_a"] in permisos
        assert regla["permiso_b"] in permisos
        assert set(caso["permisos"]) == {regla["permiso_a"], regla["permiso_b"]}
        assert caso["severidad"] == regla["severidad"]


def test_privilege_creep_deja_el_rastro_esperado(dataset_d1: dict) -> None:
    """Los accesos heredados tienen que ser directos, viejos y sin revisar.

    Es la huella que deja un cambio de puesto sin revocacion, y es lo que el
    detector busca. Si el generador los escribiera con fecha de revision o
    recientes, el motor no tendria nada que encontrar.
    """
    tablas = dataset_d1["tablas"]
    por_usuario: dict[str, list[dict]] = defaultdict(list)
    for fila in _validas(tablas):
        por_usuario[fila["legajo"]].append(fila)

    permisos_de_rol: dict[str, set[str]] = defaultdict(set)
    for fila in tablas["roles_permisos"]:
        permisos_de_rol[fila["rol_codigo"]].add(fila["permiso_codigo"])

    casos = dataset_d1["manifiesto"]["hallazgos_plantados"]["privilege_creep"]["casos"]
    assert casos, "el perfil deberia plantar al menos un caso"
    for caso in casos:
        filas = {
            normalizar_codigo(f["permiso_codigo"]): f for f in por_usuario[caso["legajo"]]
        }
        propios: set[str] = set()
        for fila in por_usuario[caso["legajo"]]:
            if fila["origen"] == OrigenAsignacion.RBAC_ROL:
                propios |= permisos_de_rol[fila["rol_codigo"]]

        assert caso["permisos_heredados"], "un caso sin permisos no es un hallazgo"
        for codigo in caso["permisos_heredados"]:
            fila = filas[codigo]
            antiguedad = (FECHA_CORTE - _fecha(fila["fecha_otorgamiento"])).days
            assert fila["origen"] == OrigenAsignacion.DIRECTA
            assert fila["fecha_revision"] == ""
            assert antiguedad > 365
            assert codigo not in propios


def test_sobre_privilegio_se_destaca_del_percentil_del_puesto(
    dataset_d1: dict,
) -> None:
    """El sobreprivilegio es relativo, asi que se verifica como tal.

    El criterio es el que va a usar el modulo de analisis: un usuario esta
    sobreprivilegiado cuando acumula al menos el doble de permisos que la
    mediana de su puesto. Se usa la mediana y no el percentil 95 porque el
    percentil de un grupo chico se corre cuando el propio grupo tiene dos o
    tres casos atipicos, y entonces los outliers terminan definiendo el
    umbral que deberia delatarlos. La mediana no se mueve por eso.

    La prueba verifica las dos direcciones: que todos los plantados superen
    el umbral y que ninguno de los demas lo alcance. Lo segundo es lo que
    convierte al manifiesto en una referencia exacta y no en un piso.
    """
    tablas = dataset_d1["tablas"]
    activas = _activas_por_legajo(tablas)
    puesto_de = {u["legajo"]: u["puesto"] for u in tablas["usuarios"]}

    por_puesto: dict[str, list[int]] = defaultdict(list)
    for legajo, permisos in activas.items():
        por_puesto[puesto_de[legajo]].append(len(permisos))

    umbral = {
        puesto: 2 * median(valores) for puesto, valores in por_puesto.items()
    }
    detectados = {
        legajo
        for legajo, permisos in activas.items()
        if len(permisos) >= umbral[puesto_de[legajo]]
    }
    plantados = _plantados(dataset_d1["manifiesto"], "sobre_privilegio")

    assert plantados
    assert detectados == plantados, (
        f"sobran {sorted(detectados - plantados)[:5]} y "
        f"faltan {sorted(plantados - detectados)[:5]}"
    )
    for caso in dataset_d1["manifiesto"]["hallazgos_plantados"]["sobre_privilegio"][
        "casos"
    ]:
        assert len(activas[caso["legajo"]]) == caso["permisos"]


# ---------------------------------------------------------------------------
# Ruido
# ---------------------------------------------------------------------------


def test_las_filas_malformadas_son_las_declaradas(dataset_d1: dict) -> None:
    tablas = dataset_d1["tablas"]
    rechazadas = len(tablas["asignaciones"]) - len(_validas(tablas))
    assert rechazadas == dataset_d1["manifiesto"]["ruido"]["filas_malformadas"]


def test_los_codigos_deformados_normalizan_a_un_permiso_existente(
    dataset_d1: dict,
) -> None:
    tablas = dataset_d1["tablas"]
    permisos = {p["codigo"] for p in tablas["permisos"]}
    alternativos = 0
    for fila in _validas(tablas):
        codigo = fila["permiso_codigo"]
        if codigo not in permisos:
            alternativos += 1
            assert normalizar_codigo(codigo) in permisos
    assert alternativos == (
        dataset_d1["manifiesto"]["ruido"]["codigos_con_nomenclatura_alternativa"]
    )


def test_los_usuarios_duplicados_comparten_legajo(dataset_d1: dict) -> None:
    legajos = [u["legajo"] for u in dataset_d1["tablas"]["usuarios"]]
    duplicados = len(legajos) - len(set(legajos))
    assert duplicados == dataset_d1["manifiesto"]["ruido"]["usuarios_duplicados"]


def test_el_ruido_no_toca_a_los_usuarios_con_hallazgo_plantado(
    dataset_d1: dict,
) -> None:
    """Condicion que sostiene la medicion: el ruido vive en los datos limpios.

    Si una fila malformada o un codigo deformado cayera sobre un usuario con
    hallazgo plantado, el sistema podria fallar en detectarlo por un problema
    de importacion y no de analisis, y la medicion atribuiria el error al
    motor equivocado.
    """
    manifiesto = dataset_d1["manifiesto"]
    marcados = {
        legajo
        for categoria in manifiesto["hallazgos_plantados"]
        for legajo in _plantados(manifiesto, categoria)
    }
    permisos = {p["codigo"] for p in dataset_d1["tablas"]["permisos"]}
    legajos_validos = {u["legajo"] for u in dataset_d1["tablas"]["usuarios"]}

    for fila in dataset_d1["tablas"]["asignaciones"]:
        if fila["legajo"] not in marcados:
            continue
        assert fila["legajo"] in legajos_validos
        assert fila["permiso_codigo"] in permisos, "codigo deformado sobre un marcado"
        assert _fecha(fila["fecha_otorgamiento"]) is not None


# ---------------------------------------------------------------------------
# Reproducibilidad y diseno de roles
# ---------------------------------------------------------------------------


def test_la_misma_semilla_produce_el_mismo_dataset(tmp_path) -> None:
    """Requisito del indicador I-05: el analisis tiene que ser repetible."""
    uno = generar("D-1", tmp_path / "a", semilla=777, fecha_corte=FECHA_CORTE)
    otro = generar("D-1", tmp_path / "b", semilla=777, fecha_corte=FECHA_CORTE)
    assert uno == otro
    for nombre in ("usuarios.csv", "asignaciones.csv", "reglas_sod.csv"):
        a = (tmp_path / "a" / "D-1" / nombre).read_bytes()
        b = (tmp_path / "b" / "D-1" / nombre).read_bytes()
        assert a == b


def test_semillas_distintas_producen_datasets_distintos(tmp_path) -> None:
    uno = generar("D-1", tmp_path / "a", semilla=1, fecha_corte=FECHA_CORTE)
    otro = generar("D-1", tmp_path / "b", semilla=2, fecha_corte=FECHA_CORTE)
    assert uno["hallazgos_plantados"] != otro["hallazgos_plantados"]


def test_ningun_rol_contiene_un_conflicto_interno(dataset_d1: dict) -> None:
    """Un rol en conflicto convertiria a todos sus asignatarios en hallazgo."""
    tablas = dataset_d1["tablas"]
    permisos_de_rol: dict[str, set[str]] = defaultdict(set)
    for fila in tablas["roles_permisos"]:
        permisos_de_rol[fila["rol_codigo"]].add(fila["permiso_codigo"])

    for rol, permisos in permisos_de_rol.items():
        for regla in tablas["reglas_sod"]:
            assert not (
                regla["permiso_a"] in permisos and regla["permiso_b"] in permisos
            ), f"el rol {rol} infringe {regla['codigo']}"


def test_perfil_desconocido_falla(tmp_path) -> None:
    with pytest.raises(ValueError, match="Perfil desconocido"):
        generar("D-9", tmp_path)
