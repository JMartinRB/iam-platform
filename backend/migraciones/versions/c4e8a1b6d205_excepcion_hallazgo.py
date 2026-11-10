"""tabla de excepciones justificadas sobre hallazgos

Implementa el requerimiento RF-11. La excepcion apunta al par
usuario-categoria y opcionalmente a la regla de Segregacion de Funciones,
y no al identificador del hallazgo: el criterio de aceptacion exige que la excepcion siga valiendo en
el diagnostico siguiente, y cada diagnostico produce filas de hallazgo
nuevas.

Revision ID: c4e8a1b6d205
Revises: b1c7d2e4f9a3
Create Date: 2026-10-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1b6d205"
down_revision: str | Sequence[str] | None = "b1c7d2e4f9a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIAS = (
    "cuenta_dormida",
    "cuenta_huerfana",
    "sobre_privilegio",
    "conflicto_sod",
    "privilege_creep",
)


def upgrade() -> None:
    valores = ", ".join(f"'{c}'" for c in CATEGORIAS)
    op.create_table(
        "excepcion_hallazgo",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("categoria", sa.String(length=30), nullable=False),
        sa.Column("regla_sod_id", sa.Integer(), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("responsable", sa.String(length=120), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revocada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocada_por", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["regla_sod_id"], ["regla_sod.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuario.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"categoria IN ({valores})", name="ck_categoria_dominio"
        ),
        sa.UniqueConstraint(
            "usuario_id", "categoria", "regla_sod_id", name="uq_excepcion_alcance"
        ),
    )
    op.create_index(
        op.f("ix_excepcion_hallazgo_usuario_id"),
        "excepcion_hallazgo",
        ["usuario_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_excepcion_hallazgo_usuario_id"), table_name="excepcion_hallazgo"
    )
    op.drop_table("excepcion_hallazgo")
