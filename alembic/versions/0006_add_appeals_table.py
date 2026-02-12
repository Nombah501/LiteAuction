"""add appeals table

Revision ID: 0006_add_appeals_table
Revises: 0005_users_gate_hint_at
Create Date: 2026-02-13 00:20:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_add_appeals_table"
down_revision: str | None = "0005_users_gate_hint_at"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appeals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appeal_ref", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("appellant_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolver_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("TIMEZONE('utc', NOW())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("TIMEZONE('utc', NOW())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'REJECTED')",
            name=op.f("ck_appeals_appeals_status_values"),
        ),
        sa.CheckConstraint(
            "source_type IN ('complaint', 'risk', 'manual')",
            name=op.f("ck_appeals_appeals_source_type_values"),
        ),
        sa.CheckConstraint(
            "((source_type = 'manual' AND source_id IS NULL) "
            "OR (source_type IN ('complaint', 'risk') AND source_id IS NOT NULL))",
            name=op.f("ck_appeals_appeals_source_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["appellant_user_id"],
            ["users.id"],
            name=op.f("fk_appeals_appellant_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolver_user_id"],
            ["users.id"],
            name=op.f("fk_appeals_resolver_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appeals")),
        sa.UniqueConstraint(
            "appellant_user_id",
            "appeal_ref",
            name=op.f("uq_appeals_appellant_user_id_appeal_ref"),
        ),
    )
    op.create_index(op.f("ix_appeals_appellant_user_id"), "appeals", ["appellant_user_id"], unique=False)
    op.create_index(op.f("ix_appeals_status"), "appeals", ["status"], unique=False)
    op.create_index("ix_appeals_status_created_at", "appeals", ["status", "created_at"], unique=False)
    op.create_index(
        "ix_appeals_source_type_source_id",
        "appeals",
        ["source_type", "source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_appeals_source_type_source_id", table_name="appeals")
    op.drop_index("ix_appeals_status_created_at", table_name="appeals")
    op.drop_index(op.f("ix_appeals_status"), table_name="appeals")
    op.drop_index(op.f("ix_appeals_appellant_user_id"), table_name="appeals")
    op.drop_table("appeals")
