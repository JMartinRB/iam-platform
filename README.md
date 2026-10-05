# IAM Insight

Plataforma de análisis, asignación y remediación de accesos en entornos IAM,
con generación asistida de scripts mediante modelos de lenguaje.

Proyecto Final de Ingeniería en Informática — Universidad del Salvador — 2026
Juan Martín Rodríguez Bruni

## Qué hace

Tres módulos sobre un mismo modelo de datos:

1. **Análisis**: a partir de un export de accesos en CSV o Excel, detecta cuentas
   dormidas, cuentas huérfanas, usuarios sobre-privilegiados, conflictos de
   Segregación de Funciones y privilege creep, con severidad y recomendación.
2. **Asignación**: ante un alta nueva resuelve los accesos que corresponden por
   rol y por atributos (RBAC + ABAC) y los valida contra el estado vigente para
   detectar combinaciones tóxicas antes del otorgamiento.
3. **Generación de scripts**: traduce la decisión ya tomada a la sintaxis de la
   plataforma destino mediante un modelo de lenguaje, con validación automática
   y aprobación humana obligatoria antes de cualquier ejecución.

El modelo de lenguaje no decide accesos: solo traduce decisiones ya validadas.

## Requisitos

- Python 3.12
- PostgreSQL 16 (o Docker)
- Node 20 (solo para el frontend)

## Instalación

```bash
git clone <repositorio> && cd iam-platform
python -m venv .venv && source .venv/bin/activate   # en Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cp .env.example .env     # completar DATABASE_URL y, si se usa, LLM_API_KEY
```

Con Docker, en lugar de lo anterior:

```bash
docker compose up
```

## Esquema de base

El esquema se crea con Alembic. Las trece entidades del modelo viven en
`backend/app/nucleo/modelos.py`, y la migración inicial se genera a partir de
ahí:

```bash
cd backend
alembic upgrade head                 # aplica el esquema
alembic revision --autogenerate -m "descripción"   # después de cambiar modelos
```

La URL de conexión no está en `alembic.ini`: sale de `DATABASE_URL`, así que no
hay credenciales versionadas.

## Datos de prueba

El repositorio no incluye datos reales de ninguna organización. Los conjuntos se
generan con hallazgos plantados de forma controlada, y eso es lo que permite
medir precisión y exhaustividad contra una verdad de referencia:

```bash
cd backend
python -m herramientas.generar_dataset --perfil D-2 --salida ../datasets
python -m herramientas.generar_dataset --todos --salida ../datasets
```

Cada perfil deja en `datasets/<perfil>/` los CSV del conjunto más un
`manifiesto.json` con los hallazgos plantados, caso por caso, y con el ruido
declarado.

| Perfil | Usuarios | Asignaciones | Uso |
|--------|---------:|-------------:|-----|
| D-1 | 500 | 5.000 | desarrollo y casos borde |
| D-2 | 2.000 | 20.000 | medición de los indicadores |
| D-3 | 5.000 | 50.000 | medición de rendimiento |

Dos propiedades sostienen la medición y están verificadas por las pruebas:
las cinco categorías plantadas son excluyentes por usuario, y nadie que no haya
sido marcado cae en ninguna de ellas por casualidad. El ruido del archivo
—nomenclaturas alternativas del mismo permiso, usuarios repetidos, campos
vacíos y filas malformadas— se escribe siempre sobre usuarios sin hallazgo, de
modo que un fallo de importación no se pueda confundir con un fallo de
detección.

La generación es determinística: la misma semilla produce byte a byte el mismo
conjunto, que es lo que exige el indicador de consistencia del plan de pruebas.

## Ejecución

```bash
cd backend
uvicorn app.main:app --reload        # API en http://localhost:8000
```

`GET /salud` devuelve el estado del servicio, de la base y el proveedor de
modelo configurado.

## Pruebas

```bash
cd backend
pytest                 # suite completa
ruff check .           # estilo
```

## Estructura

```
backend/
  app/
    nucleo/        configuración, base de datos, modelos
    modulos/       análisis, políticas, SoD, generación
    api/           endpoints REST
    main.py        punto de entrada de la API
  herramientas/    generador de datasets sintéticos
  migraciones/     revisiones de Alembic
  tests/
frontend/          interfaz web (React + TypeScript)
datasets/          conjuntos generados (no se versionan)
docs/              documentación técnica
```

## Licencia

MIT. Ver LICENSE.
