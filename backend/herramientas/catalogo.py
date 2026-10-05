"""Catalogo base del generador de datasets.

Las estructuras de este modulo definen la organizacion ficticia sobre la que
se construyen los conjuntos D-1, D-2 y D-3: que aplicaciones existen, que
funciones expone cada una, que areas y puestos tiene la empresa y que pares
de funciones son incompatibles entre si.

Se mantiene separado del generador para que los tres conjuntos compartan la
misma semantica de negocio y cambiar el tamano no cambie las reglas.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Funciones: la unidad de la que se derivan los permisos de cada aplicacion.
# El peso de riesgo va de 1 a 10 y alimenta el indice de riesgo del analisis.
# ---------------------------------------------------------------------------

FUNCIONES: dict[str, tuple[str, str, int]] = {
    "CONSULTA": ("Consulta de informacion", "lectura", 1),
    "REPORTES": ("Emision de reportes", "lectura", 2),
    "EXPORT_MASIVO": ("Exportacion masiva de datos", "lectura", 6),
    "ALTA_DATOS": ("Alta de registros maestros", "escritura", 4),
    "MODIF_DATOS": ("Modificacion de registros maestros", "escritura", 5),
    "ALTA_PROVEEDOR": ("Alta de proveedores", "escritura", 7),
    "CARGA_PAGOS": ("Carga de ordenes de pago", "escritura", 7),
    "APROB_PAGOS": ("Aprobacion de ordenes de pago", "escritura", 8),
    "CONCILIACION": ("Conciliacion de cuentas", "escritura", 6),
    "CIERRE_CONTABLE": ("Cierre contable", "escritura", 8),
    "ADMIN_USUARIOS": ("Administracion de usuarios", "administracion", 9),
    "ADMIN_CONFIG": ("Administracion de la configuracion", "administracion", 10),
}

# Perfiles de rol: conjunto de funciones que agrupa cada nivel de rol.
PERFILES_ROL: dict[str, list[str]] = {
    "BASICO": ["CONSULTA"],
    "OPERATIVO": ["CONSULTA", "REPORTES", "ALTA_DATOS", "MODIF_DATOS"],
    "SUPERVISOR": ["CONSULTA", "REPORTES", "APROB_PAGOS", "CONCILIACION"],
    "ADMIN_IDENT": ["CONSULTA", "ADMIN_USUARIOS"],
    "ADMIN_PLAT": ["CONSULTA", "REPORTES", "ADMIN_CONFIG"],
}

# ---------------------------------------------------------------------------
# Aplicaciones. El orden importa: los conjuntos chicos toman el prefijo de
# esta lista, de modo que D-1 sea un subconjunto conceptual de D-3.
# ---------------------------------------------------------------------------

APLICACIONES: list[tuple[str, str, str, str, list[str]]] = [
    # (codigo, nombre, criticidad, tecnologia, funciones)
    (
        "ERP",
        "ERP corporativo",
        "alta",
        "SAP",
        [
            "CONSULTA",
            "REPORTES",
            "ALTA_DATOS",
            "MODIF_DATOS",
            "ALTA_PROVEEDOR",
            "CARGA_PAGOS",
            "APROB_PAGOS",
            "CONCILIACION",
            "CIERRE_CONTABLE",
            "ADMIN_USUARIOS",
            "ADMIN_CONFIG",
        ],
    ),
    (
        "DIR",
        "Directorio corporativo",
        "alta",
        "Active Directory",
        ["CONSULTA", "ALTA_DATOS", "MODIF_DATOS", "ADMIN_USUARIOS", "ADMIN_CONFIG"],
    ),
    (
        "TES",
        "Sistema de tesoreria",
        "alta",
        "Aplicacion propia",
        [
            "CONSULTA",
            "REPORTES",
            "CARGA_PAGOS",
            "APROB_PAGOS",
            "CONCILIACION",
            "ADMIN_CONFIG",
        ],
    ),
    (
        "CRM",
        "Gestion comercial",
        "media",
        "Salesforce",
        ["CONSULTA", "REPORTES", "ALTA_DATOS", "MODIF_DATOS", "EXPORT_MASIVO"],
    ),
    (
        "RRH",
        "Portal de recursos humanos",
        "alta",
        "Aplicacion propia",
        ["CONSULTA", "REPORTES", "ALTA_DATOS", "MODIF_DATOS", "EXPORT_MASIVO"],
    ),
    (
        "DWH",
        "Datawarehouse",
        "media",
        "PostgreSQL",
        ["CONSULTA", "REPORTES", "EXPORT_MASIVO", "ADMIN_CONFIG"],
    ),
    (
        "FAC",
        "Facturacion electronica",
        "alta",
        "Aplicacion propia",
        ["CONSULTA", "REPORTES", "ALTA_DATOS", "MODIF_DATOS", "CIERRE_CONTABLE"],
    ),
    (
        "COM",
        "Gestion de compras",
        "media",
        "Aplicacion propia",
        ["CONSULTA", "REPORTES", "ALTA_PROVEEDOR", "ALTA_DATOS", "MODIF_DATOS"],
    ),
    (
        "DOC",
        "Gestion documental",
        "baja",
        "SharePoint",
        ["CONSULTA", "ALTA_DATOS", "MODIF_DATOS", "EXPORT_MASIVO"],
    ),
    (
        "MDA",
        "Mesa de ayuda",
        "baja",
        "Jira Service Management",
        ["CONSULTA", "REPORTES", "ALTA_DATOS", "MODIF_DATOS", "ADMIN_CONFIG"],
    ),
    (
        "SEG",
        "Consola de seguridad",
        "alta",
        "Aplicacion propia",
        ["CONSULTA", "REPORTES", "ADMIN_USUARIOS", "ADMIN_CONFIG"],
    ),
    (
        "NOM",
        "Liquidacion de sueldos",
        "alta",
        "Aplicacion propia",
        ["CONSULTA", "REPORTES", "CARGA_PAGOS", "APROB_PAGOS", "CIERRE_CONTABLE"],
    ),
    (
        "ANL",
        "Analitica de negocio",
        "baja",
        "Power BI",
        ["CONSULTA", "REPORTES", "EXPORT_MASIVO"],
    ),
    (
        "INV",
        "Gestion de inventario",
        "media",
        "Aplicacion propia",
        ["CONSULTA", "REPORTES", "ALTA_DATOS", "MODIF_DATOS"],
    ),
]

# ---------------------------------------------------------------------------
# Estructura organizacional. Cada puesto declara los roles que le
# corresponden como linea de base (aplicacion + perfil de rol).
# ---------------------------------------------------------------------------

PUESTOS: list[tuple[str, str, list[tuple[str, str]]]] = [
    # (area, puesto, [(aplicacion, perfil)])
    ("Finanzas", "Analista contable", [("ERP", "OPERATIVO"), ("FAC", "OPERATIVO")]),
    ("Finanzas", "Analista de pagos", [("ERP", "OPERATIVO"), ("TES", "OPERATIVO")]),
    ("Finanzas", "Tesorero", [("TES", "SUPERVISOR")]),
    (
        "Finanzas",
        "Jefe de administracion",
        [("ERP", "SUPERVISOR"), ("FAC", "SUPERVISOR")],
    ),
    ("Comercial", "Ejecutivo de cuentas", [("CRM", "OPERATIVO")]),
    ("Comercial", "Jefe comercial", [("CRM", "SUPERVISOR"), ("ANL", "BASICO")]),
    ("Recursos Humanos", "Analista de RRHH", [("RRH", "OPERATIVO")]),
    ("Recursos Humanos", "Liquidador de sueldos", [("NOM", "OPERATIVO")]),
    (
        "Recursos Humanos",
        "Jefe de RRHH",
        [("RRH", "SUPERVISOR"), ("NOM", "SUPERVISOR")],
    ),
    (
        "Tecnologia",
        "Administrador de sistemas",
        [("DIR", "ADMIN_IDENT"), ("MDA", "ADMIN_PLAT")],
    ),
    ("Tecnologia", "Desarrollador", [("DOC", "OPERATIVO"), ("MDA", "OPERATIVO")]),
    ("Tecnologia", "Analista de soporte", [("MDA", "OPERATIVO"), ("DIR", "BASICO")]),
    ("Tecnologia", "Analista de seguridad", [("SEG", "ADMIN_IDENT"), ("DIR", "BASICO")]),
    ("Compras", "Comprador", [("COM", "OPERATIVO"), ("INV", "OPERATIVO")]),
    ("Compras", "Jefe de compras", [("COM", "SUPERVISOR"), ("ERP", "BASICO")]),
    ("Operaciones", "Analista de operaciones", [("INV", "OPERATIVO"), ("DOC", "BASICO")]),
    ("Auditoria interna", "Auditor interno", [("DWH", "BASICO"), ("ERP", "BASICO")]),
    ("Legales", "Analista legal", [("DOC", "OPERATIVO")]),
]

UBICACIONES = [
    "Buenos Aires",
    "Cordoba",
    "Rosario",
    "Mendoza",
    "Montevideo",
    "Remoto",
]

# ---------------------------------------------------------------------------
# Reglas de Segregacion de Funciones, expresadas sobre funciones y no sobre
# permisos concretos. El generador las instancia para cada combinacion de
# aplicaciones que las haga aplicables.
# ---------------------------------------------------------------------------

REGLAS_SOD: list[tuple[str, str, str, str]] = [
    # (funcion_a, funcion_b, severidad, fundamento)
    (
        "CARGA_PAGOS",
        "APROB_PAGOS",
        "critica",
        "Quien registra una orden de pago no puede autorizarla: habilita el "
        "desvio de fondos sin intervencion de un tercero.",
    ),
    (
        "ALTA_PROVEEDOR",
        "CARGA_PAGOS",
        "critica",
        "Permite dar de alta un proveedor ficticio y pagarle sin control "
        "cruzado sobre el maestro de proveedores.",
    ),
    (
        "ADMIN_USUARIOS",
        "APROB_PAGOS",
        "critica",
        "Quien administra identidades podria otorgarse a si mismo la "
        "aprobacion de pagos y revertir el cambio despues.",
    ),
    (
        "ALTA_DATOS",
        "CONCILIACION",
        "alta",
        "Quien carga los asientos no deberia conciliarlos, porque puede "
        "ocultar diferencias que el mismo origino.",
    ),
    (
        "MODIF_DATOS",
        "CIERRE_CONTABLE",
        "alta",
        "Modificar registros maestros y cerrar el periodo contable en la "
        "misma persona anula el control de integridad del cierre.",
    ),
    (
        "ADMIN_CONFIG",
        "EXPORT_MASIVO",
        "media",
        "Quien configura la aplicacion puede desactivar el registro de "
        "auditoria y despues extraer datos sin dejar rastro.",
    ),
    (
        "ADMIN_USUARIOS",
        "ADMIN_CONFIG",
        "media",
        "Concentra la administracion de identidades y de la plataforma en un "
        "unico operador sin control por oposicion.",
    ),
]
