"""add points ledger for rewards

Revision ID: 0013_add_points_ledger
Revises: 0012_add_guarantor_requests
Create Date: 2026-02-13 18:40:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013_add_points_ledger"
down_revision: str | None = "0012_add_guarantor_requests"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    points_event_type = postgresql.ENUM(
        "FEEDBACK_APPROVED",
        "MANUAL_ADJUSTMENT",
        name="points_event_type",
    )
    points_event_type.create(op.get_bind(), checkfirst=True)

    points_event_type_column = postgresql.ENUM(
        "FEEDBACK_APPROVED",
        "MANUAL_ADJUSTMENT",
        name="points_event_type",
        create_type=False,
    )

    op.create_table(
        "points_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("event_type", points_event_type_column, nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint("amount <> 0", name="points_ledger_amount_nonzero"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_points_ledger_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_points_ledger")),
        sa.UniqueConstraint("dedupe_key", name="uq_points_ledger_dedupe_key"),
    )
    op.create_index(
        "ix_points_ledger_user_created_at",
        "points_ledger",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_points_ledger_user_created_at", table_name="points_ledger")
    op.drop_table("points_ledger")
    op.execute("DROP TYPE IF EXISTS points_event_type")
