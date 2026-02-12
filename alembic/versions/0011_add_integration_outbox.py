"""add integration outbox for feedback github automation

Revision ID: 0011_add_integration_outbox
Revises: 0010_add_feedback_items
Create Date: 2026-02-13 12:20:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011_add_integration_outbox"
down_revision: str | None = "0010_add_feedback_items"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'CREATE_FEEDBACK_GITHUB_ISSUE'")

    integration_outbox_status = postgresql.ENUM(
        "pending",
        "done",
        "failed",
        name="integration_outbox_status",
    )
    integration_outbox_status.create(op.get_bind(), checkfirst=True)

    integration_outbox_status_column = postgresql.ENUM(
        "pending",
        "done",
        "failed",
        name="integration_outbox_status",
        create_type=False,
    )

    op.create_table(
        "integration_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("TIMEZONE('utc', NOW())"),
            nullable=False,
        ),
        sa.Column(
            "status",
            integration_outbox_status_column,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_outbox")),
        sa.UniqueConstraint("dedupe_key", name="uq_integration_outbox_dedupe_key"),
    )
    op.create_index(
        "ix_integration_outbox_status_next_retry_at",
        "integration_outbox",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_integration_outbox_status_next_retry_at", table_name="integration_outbox")
    op.drop_table("integration_outbox")

    op.execute("DROP TYPE IF EXISTS integration_outbox_status")

    # PostgreSQL enum value removal is intentionally unsupported for moderation_action.
