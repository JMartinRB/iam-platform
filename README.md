<img src="docs/logo-saga.png" alt="SAGA" width="330">

# SAGA · Sistema de Análisis y Gestión de Accesos

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
- PostgreSQL 16 (opcional: por omisión usa un archivo SQLite local)

La interfaz web no necesita Node ni paso de compilación: es HTML, CSS y
JavaScript que el navegador interpreta tal como están en el repositorio.

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

## Análisis

Una vez cargado un dataset, el módulo de análisis aplica las cinco reglas de
detección y produce hallazgos con severidad, índice de riesgo, evidencia y
recomendación. El plan de pruebas completo se ejecuta con:

```bash
cd backend
python -m herramientas.medir --url "$DATABASE_URL" --salida medicion.json
```

Medición sobre PostgreSQL 16, con los conjuntos generados con la semilla por
omisión:

| Indicador | Umbral | D-1 | D-2 | D-3 |
|-----------|--------|-----|-----|-----|
| I-01 Tiempo de análisis | < 60 s | 0,21 s | 0,83 s | 2,02 s (p95 2,23 s) |
| I-02 Precisión | > 90 % | 100 % | 100 % | 100 % |
| I-02 Exhaustividad | > 85 % | 100 % | 100 % | 100 % |
| I-03 Conflictos interceptados | 100 % | 16/16 | 17/17 | 19/19 |
| I-05 Consistencia | sin variación | — | 3 corridas idénticas | — |

Sobre la interpretación de esos valores: el generador y el detector comparten
las definiciones de cada categoría, así que la medición verifica que las
reglas estén implementadas como se especificaron y que el recorrido completo
—importación, normalización, análisis— no pierda ni invente hallazgos. No
mide el desempeño del detector frente a datos reales, que tienen ambigüedades
que un conjunto sintético no reproduce.

## Asignación

Ante un alta o un cambio de puesto, el motor de políticas resuelve los accesos
en tres pasos: los roles del puesto proponen el conjunto base (RBAC), las
políticas de atributos suman o niegan permisos (ABAC), y la validación cruzada
de Segregación de Funciones revisa el resultado contra la matriz de
incompatibilidades **y contra lo que la persona ya tiene**. Ese último cruce es
la diferencia con un formulario de alta: el permiso nuevo puede ser inofensivo
y volverse incompatible por algo que la persona arrastra de su puesto anterior.

Las condiciones ABAC se guardan como expresiones en JSON y se evalúan con un
intérprete acotado propio, que soporta comparaciones, pertenencia a conjuntos y
operadores lógicos. No usa `eval`: en un sistema de control de accesos, la
política que decide quién accede a qué no puede ser a su vez un vector de
ejecución de código.

```json
{"y": [{"igual": ["area", "Finanzas"]}, {"mayor": ["antiguedad_dias", 180]}]}
```

Cuando el catálogo de roles no declara a qué puesto apunta cada rol —lo
habitual en una organización real— el perfil se infiere de lo que efectivamente
tienen los pares del mismo puesto, con un umbral del 60 %.

## Generación de scripts

El modelo de lenguaje no decide accesos: recibe un conjunto cerrado de cambios
que el motor de políticas ya resolvió y su única tarea es escribirlos en la
sintaxis de la plataforma destino. La salida atraviesa cinco controles
automáticos antes de poder aprobarse, y un sexto que es la aprobación humana:

| # | Control | Qué rechaza |
|---|---------|-------------|
| 1 | Esquema | Respuestas que no cumplen la estructura JSON definida |
| 2 | Sintaxis | Scripts que no parsean en su propio lenguaje |
| 3 | Lista blanca | Operaciones fuera de un conjunto cerrado |
| 4 | Lista negra | Patrones prohibidos aunque la lista blanca los permitiera |
| 5 | Correspondencia | Identificadores que no están en la decisión que originó el script |
| 6 | Aprobación humana | Nada se aplica sin que una persona lo apruebe |

El quinto es el más específico del dominio y el que ataca de frente la
alucinación: no evalúa si la salida es plausible sino si coincide con una
decisión previa y verificable. Un grupo inventado, o uno real que no
correspondía a esta decisión, cae ahí.

El contexto que sale del entorno va seudonimizado —identificadores internos, no
legajos ni nombres ni correos— y los valores del dataset se sanean e insertan en
un bloque delimitado que nunca se concatena con el prompt del sistema.

Plataformas destino implementadas: PowerShell sobre Active Directory y llamadas
REST a la API de Okta. Proveedores: Gemini, Groq, Ollama como respaldo local, y
un proveedor simulado que permite recorrer el sistema completo sin credenciales.

### Medir el indicador I-04

Requiere una clave real: medir contra el proveedor simulado mediría el propio
simulador. La clave se configura en el `.env` local y nunca se versiona.

```bash
cd backend
python -m herramientas.medir_generacion --casos 100
```

## Ejecución

```bash
cd backend
uvicorn app.main:app --reload
```

Después, `http://localhost:8000` abre la interfaz y `/docs` la documentación
de la API. Con una base SQLite el esquema se crea solo en el primer arranque;
contra PostgreSQL el esquema lo lleva Alembic (`alembic upgrade head`).

`GET /api/salud` devuelve el estado del servicio, de la base y el proveedor de
modelo configurado.

### La interfaz

Cinco pantallas, construidas sobre las maquetas validadas con usuarios:

| Pantalla | Qué hace |
|---|---|
| Carga de datasets | Importa un export de ocho CSV, o genera un conjunto sintético y lo carga |
| Panel de hallazgos | Diagnóstico con índice de riesgo, filtro por severidad y evidencia de cada hallazgo |
| Altas pendientes | Resuelve los accesos de un alta, genera el script y lo somete a aprobación |
| Scripts generados | Historial con el resultado de los controles de cada salida |
| Registro de auditoría | Qué hizo el sistema y quién lo autorizó |

El paso de generación usa por omisión un **proveedor simulado**, que arma la
respuesta a partir del propio contexto y no llama a ningún modelo de lenguaje.
La pantalla lo informa. Para usar el proveedor configurado en el `.env` hay que
marcar la casilla correspondiente antes de generar.

### Recorrido narrado por consola

```bash
cd backend
python -m herramientas.demostracion              # avanza con Enter
python -m herramientas.demostracion --corrido    # de punta a punta
```

## Pruebas

```bash
cd backend
pytest                 # suite completa
ruff check .           # estilo
mypy                   # tipos
```

GitHub Actions corre las tres cosas en cada push y en cada pull request contra
`main`; un fallo bloquea la integración.

La suite tiene 255 pruebas. La cobertura es del 90 % sobre `app` y del 78 %
contando también `herramientas`; la diferencia se explica porque los scripts de
medición y el recorrido narrado son herramientas de línea de comandos que no se
ejercitan desde las pruebas.

## Estructura

```
backend/
  app/
    nucleo/        configuración, base de datos, modelos
    modulos/       análisis, políticas, SoD, generación
    api/           endpoints REST (sin lógica de negocio)
    main.py        punto de entrada de la API y de la interfaz
  herramientas/    generador de datasets, medición y recorrido narrado
  migraciones/     revisiones de Alembic
  tests/
frontend/          interfaz web (HTML, CSS y JavaScript, sin compilación)
datasets/          conjuntos generados (no se versionan)
docs/              documentación técnica
```

## Licencia

MIT. Ver LICENSE.
