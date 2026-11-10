"""solicitada_por pasa a ser obligatoria

La regla de que nadie puede aprobar una solicitud que el mismo origino se
evalua comparando ``solicitada_por`` contra quien aprueba. Con la columna
nullable, una solicitud sin autor declarado pasaba el control por no tener
con que compararse. Que la base lo rechace cierra el caso tambien para las
escrituras que no pasen por la aplicacion.

SQLite no admite ALTER COLUMN, asi que el cambio va dentro de un
``batch_alter_table``: Alembic recrea la tabla con la definicion nueva y
copia los datos.

Revision ID: b1c7d2e4f9a3
Revises: 45a028987ce3
Create Date: 2026-10-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c7d2e4f9a3"
down_revision: str | Sequence[str] | None = "45a028987ce3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RELLENO = "desconocido"


def upgrade() -> None:
    # Las filas anteriores sin autor no se pueden adivinar. Se las marca
    # explicitamente en lugar de inventarles uno: "desconocido" nunca va a
    # coincidir con el legajo de quien aprueba, asi que el control sigue
    # comportandose como corresponde sobre los datos viejos.
    op.execute(
        sa.text(
            "UPDATE solicitud_alta SET solicitada_por = :relleno "
            "WHERE solicitada_por IS NULL"
        ).bindparams(relleno=RELLENO)
    )
    with op.batch_alter_table("solicitud_alta") as lote:
        lote.alter_column(
            "solicitada_por",
            existing_type=sa.String(length=120),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("solicitud_alta") as lote:
        lote.alter_column(
            "solicitada_por",
            existing_type=sa.String(length=120),
            nullable=True,
        )
