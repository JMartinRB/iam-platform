"""Textos de las cinco categorias de hallazgo.

Viven acá y no en la pantalla por dos razones. La primera es que el titulo
y la descripcion de una categoria son parte de lo que el sistema afirma, no
decoracion: si el motor cambia el criterio, el texto tiene que cambiar con
el. La segunda es el umbral, que se arma con los parametros efectivamente
usados en la corrida y no con un numero escrito a mano, para que la
pantalla no pueda contar una cosa distinta de la que el motor calculo.

La maqueta de la Figura 4.4 del documento enumera siete categorias. El
motor implementa cinco: las dos restantes, cuentas compartidas y cuentas de
servicio sin responsable, surgieron de la entrevista al especialista y
quedaron declaradas como trabajo futuro en el capitulo de conclusiones. La
pantalla muestra lo que el motor detecta.
"""
from __future__ import annotations

from app.modulos.analisis import ParametrosAnalisis
from app.nucleo.enums import Categoria

TITULOS: dict[str, str] = {
    Categoria.CONFLICTO_SOD: "Conflicto de Segregación de Funciones",
    Categoria.CUENTA_HUERFANA: "Cuentas huérfanas con privilegio",
    Categoria.SOBRE_PRIVILEGIO: "Usuarios sobre-privilegiados",
    Categoria.PRIVILEGE_CREEP: "Privilege creep",
    Categoria.CUENTA_DORMIDA: "Cuentas dormidas",
}

DESCRIPCIONES: dict[str, str] = {
    Categoria.CONFLICTO_SOD: (
        "Usuarios que tienen a la vez dos permisos declarados incompatibles "
        "en la matriz de Segregación de Funciones."
    ),
    Categoria.CUENTA_HUERFANA: (
        "Cuentas con accesos vigentes cuyo titular ya no figura activo en el "
        "padrón de identidades."
    ),
    Categoria.SOBRE_PRIVILEGIO: (
        "Usuarios cuyo volumen de accesos supera un múltiplo de la mediana "
        "de su propio puesto."
    ),
    Categoria.PRIVILEGE_CREEP: (
        "Permisos directos, sin rol que los respalde, conservados más allá "
        "del plazo de revisión."
    ),
    Categoria.CUENTA_DORMIDA: (
        "Cuentas sin actividad registrada en el período configurado, con sus "
        "accesos todavía activos."
    ),
}

# Orden de presentación del panel: primero lo que se remedia antes.
ORDEN: list[str] = [
    Categoria.CONFLICTO_SOD,
    Categoria.CUENTA_HUERFANA,
    Categoria.SOBRE_PRIVILEGIO,
    Categoria.PRIVILEGE_CREEP,
    Categoria.CUENTA_DORMIDA,
]


def umbral_de(categoria: str, p: ParametrosAnalisis) -> str:
    """Umbral con el que se levantó la categoría, en palabras.

    Se arma con los parámetros de la corrida: si alguien cambia un umbral
    en el .env, el texto de la pantalla cambia con él.
    """
    if categoria == Categoria.CUENTA_DORMIDA:
        return (
            f"Sin acceso registrado en {p.dias_cuenta_dormida} días, "
            f"contados contra la fecha del snapshot."
        )
    if categoria == Categoria.SOBRE_PRIVILEGIO:
        return (
            f"Más de {p.factor_sobre_privilegio:g} veces la mediana de "
            f"permisos de su puesto, sobre puestos de al menos "
            f"{p.minimo_pares} personas."
        )
    if categoria == Categoria.PRIVILEGE_CREEP:
        return (
            f"Asignación directa, sin rol, otorgada hace más de "
            f"{p.antiguedad_creep_dias} días y sin revisión posterior."
        )
    if categoria == Categoria.CONFLICTO_SOD:
        return (
            "Dos permisos activos que una regla de la matriz declara "
            "incompatibles entre sí."
        )
    if categoria == Categoria.CUENTA_HUERFANA:
        return "Asignación activa cuyo usuario titular no está vigente."
    return "Sin umbral declarado."
