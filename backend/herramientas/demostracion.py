"""Recorrido completo del sistema, narrado paso a paso.

Corre los tres modulos de punta a punta sobre un conjunto sintetico y va
mostrando en pantalla que hace cada uno y con que resultado. Esta pensado
para mostrarse en vivo: no pide base de datos, no pide credenciales y
termina en menos de un minuto.

    python -m herramientas.demostracion                 avanza con Enter
    python -m herramientas.demostracion --corrido       de punta a punta
    python -m herramientas.demostracion --modelo-real   usa el modelo del .env

Sin --modelo-real el paso 7 no llama a ningun modelo: usa el proveedor
simulado, que arma la respuesta a partir del propio contexto. El paso lo
informa en pantalla, para no presentar como salida de un modelo algo que
no lo es.

Lo que recorre, en orden: generacion del conjunto de prueba, importacion con
filas defectuosas, diagnostico de los cinco tipos de hallazgo, contraste
contra la verdad de referencia, resolucion de un alta limpia, resolucion de
un alta con conflicto de Segregacion de Funciones, generacion de un script,
rechazo de dos salidas maliciosas y aprobacion humana.
"""
from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modulos.analisis import ParametrosAnalisis, analizar
from app.modulos.generacion import (
    aprobar_script,
    generar_script,
    registrar_script,
    seudonimo,
)
from app.modulos.importacion import importar_dataset
from app.modulos.politicas import (
    registrar_solicitud,
    resolucion_a_permisos,
    resolver_alta,
)
from app.modulos.proveedores import (
    Proveedor,
    ProveedorSimulado,
    obtener_proveedor,
)
from app.modulos.validacion import validar_salida
from app.nucleo.base_datos import Base, crear_motor
from app.nucleo.enums import EstadoUsuario
from app.nucleo.modelos import Permiso, ReglaSoD, Usuario
from herramientas.generar_dataset import generar

CORTE = date(2026, 10, 1)
ANCHO = 74


def titulo(numero: int, texto: str) -> None:
    print()
    print("=" * ANCHO)
    print(f" PASO {numero} — {texto}")
    print("=" * ANCHO)


def dato(etiqueta: str, valor: object) -> None:
    print(f"   {etiqueta:<42}{valor}")


# Con pausas, el recorrido avanza cuando la persona aprieta Enter, para poder
# explicar cada paso mientras se muestra. Sin pausas corre de punta a punta en
# unos segundos, que es lo que hace falta cuando se lo invoca desde un script.
# Si la entrada no es una terminal (salida redirigida, ejecucion automatica),
# no se espera a nadie.
ESPERAR = True


def pausa() -> None:
    if not ESPERAR or not sys.stdin.isatty():
        time.sleep(0.4)
        return
    with contextlib.suppress(EOFError):
        input("\n   [Enter para continuar]")


def elegir_proveedor() -> tuple[Proveedor, str]:
    """Decide con que se genera el script y lo dice en voz alta.

    Por defecto usa el proveedor simulado, que arma la respuesta a partir
    del propio contexto. Sirve para recorrer el sistema sin credenciales y
    sin depender de la red, pero NO es un modelo de lenguaje, y el paso 7
    lo informa para que nadie confunda una cosa con la otra.

    Con --modelo-real se usa el proveedor configurado en el .env. Si la
    llamada falla, se avisa y se sigue con el simulado: en una demostracion
    en vivo es preferible eso a que el recorrido se corte.
    """
    if "--modelo-real" not in sys.argv:
        return ProveedorSimulado(), "simulado (no hay llamada a un modelo)"

    try:
        proveedor = obtener_proveedor()
    except Exception as error:
        print(f"   [aviso] no se pudo usar el modelo real: {error}")
        print("   [aviso] se sigue con el proveedor simulado.")
        return ProveedorSimulado(), "simulado (fallo el modelo real)"

    nombre = getattr(proveedor, "nombre", "desconocido")
    modelo = getattr(proveedor, "modelo", "")
    return proveedor, f"{nombre} / {modelo}".rstrip(" /")


def main() -> None:
    global ESPERAR
    if "--corrido" in sys.argv:
        ESPERAR = False

    print()
    print("Plataforma de analisis, asignacion y remediacion de accesos IAM")
    print("Proyecto Final — Juan Martin Rodriguez Bruni — USAL")
    if ESPERAR and sys.stdin.isatty():
        print()
        print("El recorrido tiene nueve pasos y avanza con Enter.")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        carpeta = Path(tmp)

        # -------------------------------------------------------------- 1
        titulo(1, "Conjunto de datos sintetico")
        print("   Los datos son sinteticos. No se usa informacion real de")
        print("   ninguna organizacion. Se plantan hallazgos conocidos, para")
        print("   poder medir despues cuantos detecta el sistema.")
        print()
        manifiesto = generar("D-1", carpeta, fecha_corte=CORTE)
        v = manifiesto["volumen"]
        dato("usuarios", v["usuarios"])
        dato("asignaciones de acceso", v["asignaciones"])
        dato("reglas de Segregacion de Funciones", v["reglas_sod"])
        dato("politicas de atributos", v["politicas_abac"])
        print()
        print("   Hallazgos plantados a proposito:")
        for categoria, cantidad in manifiesto["totales_plantados"].items():
            dato(f"  {categoria}", cantidad)
        pausa()

        # -------------------------------------------------------------- 2
        titulo(2, "Importacion")
        print("   El archivo trae defectos a proposito: permisos escritos de")
        print("   varias formas, usuarios repetidos y filas rotas.")
        print()
        motor = crear_motor(f"sqlite+pysqlite:///{carpeta / 'demo.db'}")
        Base.metadata.create_all(motor)
        sesion: Session = sessionmaker(bind=motor, expire_on_commit=False)()

        inicio = time.perf_counter()
        importacion = importar_dataset(sesion, carpeta / "D-1", actor="U00001")
        sesion.commit()
        dato("tiempo de carga", f"{time.perf_counter() - inicio:.2f} s")
        dato("filas leidas", importacion.leidas)
        dato("filas aceptadas", importacion.aceptadas)
        dato("filas rechazadas", importacion.descartadas)
        print()
        print("   Cada rechazo dice donde estaba y por que:")
        for rechazo in importacion.rechazos[-5:]:
            print(f"     {rechazo}")
        print()
        print("   Las variantes de escritura del mismo permiso se resolvieron")
        print(f"   solas: {manifiesto['ruido']['codigos_con_nomenclatura_alternativa']} "
              "filas normalizadas sin perder ninguna.")
        pausa()

        # -------------------------------------------------------------- 3
        titulo(3, "Diagnostico")
        inicio = time.perf_counter()
        hallazgos = analizar(
            sesion, importacion.snapshot_id, ParametrosAnalisis(fecha_corte=CORTE)
        )
        sesion.commit()
        dato("tiempo de analisis", f"{time.perf_counter() - inicio:.2f} s")
        dato("hallazgos detectados", len(hallazgos))
        print()
        por_categoria: dict[str, int] = {}
        for h in hallazgos:
            por_categoria[h.categoria] = por_categoria.get(h.categoria, 0) + 1
        for categoria, cantidad in sorted(por_categoria.items()):
            dato(f"  {categoria}", cantidad)

        print()
        print("   Los tres de mayor riesgo:")
        for h in sorted(hallazgos, key=lambda x: float(x.puntaje), reverse=True)[:3]:
            print(f"     [{h.severidad.upper():<8}] {h.categoria:<18} "
                  f"riesgo {float(h.puntaje):>6.0f}  {h.evidencia['legajo']}")
            print(f"        {(h.recomendacion or '')[:62]}...")
        pausa()

        # -------------------------------------------------------------- 4
        titulo(4, "Contraste contra la verdad de referencia")
        print("   Se comparan los hallazgos devueltos contra los que se")
        print("   plantaron. Es lo que vuelve medible al motor de deteccion.")
        print()
        print("   La comparacion es por usuario, no por hallazgo: lo que se")
        print("   exige es que el conjunto de usuarios senalados sea igual al")
        print("   conjunto plantado. Un mismo usuario puede generar mas de un")
        print("   hallazgo de la misma categoria, por eso estos numeros pueden")
        print("   ser menores que los del paso anterior.")
        print()
        print(f"   {'categoria':<22}{'senalados':>10}{'esperados':>11}"
              f"{'hallazgos':>11}{'precision':>11}{'exhaust.':>10}")
        for categoria, datos in manifiesto["hallazgos_plantados"].items():
            detectado = {h.evidencia["legajo"] for h in hallazgos
                         if h.categoria == categoria}
            plantado = {c["legajo"] for c in datos["casos"]}
            emitidos = sum(1 for h in hallazgos if h.categoria == categoria)
            ciertos = len(detectado & plantado)
            precision = ciertos / len(detectado) if detectado else 0
            exhaust = ciertos / len(plantado) if plantado else 0
            print(f"   {categoria:<22}{len(detectado):>10}{len(plantado):>11}"
                  f"{emitidos:>11}{precision:>10.0%}{exhaust:>10.0%}")
        print()
        print("   senalados: usuarios que el motor marco en esa categoria.")
        print("   esperados: usuarios plantados en el conjunto de prueba.")
        print("   hallazgos: registros emitidos sobre esos usuarios.")
        pausa()

        # -------------------------------------------------------------- 5
        titulo(5, "Alta de acceso: caso sin conflictos")
        # .one() y no .first(): si el conjunto de prueba cambiara y no hubiera
        # ninguna persona con ese puesto, conviene que el recorrido se detenga
        # con un error claro en lugar de seguir con un None a cuestas.
        usuario = sesion.scalars(
            select(Usuario)
            .where(
                Usuario.puesto == "Analista contable",
                Usuario.estado == EstadoUsuario.ACTIVO,
            )
            .limit(1)
        ).one()
        resolucion = resolver_alta(
            sesion, usuario, fecha_corte=CORTE, conservar_vigentes=False
        )
        dato("persona", f"{usuario.legajo} — {usuario.puesto} ({usuario.area})")
        dato("roles del puesto", ", ".join(resolucion.roles))
        dato("permisos resueltos", len(resolucion.otorgados))
        print()
        for permiso in resolucion.otorgados[:5]:
            print(f"     {permiso.codigo:<26} por {permiso.origen} ({permiso.rol})")
        pausa()

        # -------------------------------------------------------------- 6
        titulo(6, "Alta de acceso: caso con conflicto")
        print("   Se pide a mano un permiso incompatible con uno que la")
        print("   persona ya tiene. El conflicto no esta en el pedido: esta")
        print("   en el cruce con lo que ya tenia. Un formulario no lo ve.")
        print()
        permisos = {p.id: p for p in sesion.scalars(select(Permiso)).all()}
        regla = sesion.scalars(
            select(ReglaSoD).where(ReglaSoD.severidad == "critica").limit(1)
        ).one()
        pedido = permisos[regla.permiso_b_id].codigo

        conflictiva = resolver_alta(
            sesion,
            usuario,
            fecha_corte=CORTE,
            conservar_vigentes=False,
            permisos_solicitados=[permisos[regla.permiso_a_id].codigo, pedido],
        )
        excluido = next(e for e in conflictiva.excluidos if e.motivo == "conflicto_sod")
        dato("permiso solicitado", excluido.codigo)
        dato("resultado", "BLOQUEADO antes del otorgamiento")
        dato("regla infringida", excluido.regla)
        dato("incompatible con", excluido.permiso_en_conflicto)
        dato("severidad", excluido.severidad)

        solicitud = registrar_solicitud(
            sesion, usuario, conflictiva, solicitada_por="U00002"
        )
        sesion.commit()
        pausa()

        # -------------------------------------------------------------- 7
        titulo(7, "Generacion del script")
        print("   El modelo no decide accesos: recibe la decision ya tomada y")
        print("   solo la escribe en la sintaxis de la plataforma destino.")
        print("   El contexto viaja seudonimizado.")
        print()
        cambios = resolucion_a_permisos(resolucion)
        dato("cambios a aplicar", len(cambios))
        dato("identidad enviada al modelo", seudonimo(usuario.legajo))
        dato("legajo real enviado", "no sale del entorno")

        proveedor, descripcion_proveedor = elegir_proveedor()
        dato("proveedor", descripcion_proveedor)

        resultado = generar_script(cambios, usuario, proveedor=proveedor)

        # Si el modelo real no llego a una salida valida —se cayo la red, la
        # clave vencio, el modelo devolvio cualquier cosa tres veces— el
        # recorrido sigue con el simulado en lugar de cortarse. Se avisa: el
        # fallo es informacion, no algo que convenga esconder.
        if not isinstance(proveedor, ProveedorSimulado) and (
            not resultado.aprobado or resultado.validacion is None
        ):
            print(f"   [aviso] el modelo real no produjo una salida valida: "
                  f"{resultado.error or 'sin detalle'}")
            print("   [aviso] se repite el paso con el proveedor simulado.")
            dato("proveedor", "simulado (fallo el modelo real)")
            resultado = generar_script(
                cambios, usuario, proveedor=ProveedorSimulado()
            )

        if not resultado.aprobado or resultado.validacion is None:
            raise RuntimeError(
                "la generacion no paso la cadena de controles: "
                f"{resultado.error or 'sin detalle'}"
            )
        print()
        print("   Script generado:")
        for linea in resultado.script.splitlines()[:4]:
            print(f"     {linea}")
        print()
        print("   Controles automaticos:")
        for control in resultado.validacion.controles:
            print(f"     [OK] {control.nombre:<18} {control.detalle[:46]}")
        pausa()

        # -------------------------------------------------------------- 8
        titulo(8, "Que pasa con una salida maliciosa")
        print("   Se fuerzan tres salidas que un modelo podria producir.")
        autorizados = {seudonimo(usuario.legajo)} | {
            f"GRP_{c.permiso_codigo}" for c in cambios
        }
        grupo = next(iter(sorted(autorizados - {seudonimo(usuario.legajo)})))

        casos = [
            (
                "operacion fuera de la lista blanca",
                f"Add-ADGroupMember -Identity {grupo} "
                f"-Members {seudonimo(usuario.legajo)}\n"
                f"New-ADUser -Name {seudonimo(usuario.legajo)}",
            ),
            (
                "patron prohibido",
                f"Add-ADGroupMember -Identity {grupo} "
                f"-Members {seudonimo(usuario.legajo)} > C:\\temp\\log.txt",
            ),
            (
                "grupo inventado por el modelo",
                f"Add-ADGroupMember -Identity {grupo} "
                f"-Members {seudonimo(usuario.legajo)}\n"
                f"Add-ADGroupMember -Identity GRP_DOMAIN_ADMINS "
                f"-Members {seudonimo(usuario.legajo)}",
            ),
        ]
        for descripcion, salida in casos:
            bruto = json.dumps(
                {"lenguaje": "powershell", "script": salida, "explicacion": ""}
            )
            validacion = validar_salida(bruto, "powershell", autorizados)
            fallado = next(c for c in validacion.controles if not c.aprobado)
            print()
            dato(descripcion, "RECHAZADO")
            dato("  control que lo detuvo", fallado.nombre)
            dato("  detalle", ", ".join(fallado.hallazgos) or fallado.detalle)
        pausa()

        # -------------------------------------------------------------- 9
        titulo(9, "Aprobacion humana y auditoria")
        print("   Ningun script se aplica sin que una persona lo apruebe.")
        print()
        script = registrar_script(
            sesion, resultado, actor="U00002", solicitud_id=solicitud.id
        )
        aprobar_script(sesion, script, aprobado_por="U00009")
        sesion.commit()
        dato("script", f"#{script.id} — {script.estado_validacion}")
        dato("aprobado por", script.aprobado_por)
        dato("quien lo solicito", solicitud.solicitada_por)
        print()
        print("   Nadie puede aprobar una solicitud que el mismo origino:")
        print("   es la misma separacion de funciones que el sistema audita,")
        print("   aplicada al propio sistema.")

        from app.nucleo.modelos import EventoAuditoria

        eventos = sesion.scalars(select(EventoAuditoria)).all()
        print()
        dato("eventos de auditoria registrados", len(eventos))
        for evento in eventos[:6]:
            print(f"     {evento.actor:<10}{evento.accion:<24}{evento.entidad}")

        sesion.close()
        motor.dispose()

    print()
    print("=" * ANCHO)
    print(" Fin del recorrido.")
    print("=" * ANCHO)
    print()


if __name__ == "__main__":
    main()
