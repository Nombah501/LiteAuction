"""add guarantor request intake table

Revision ID: 0012_add_guarantor_requests
Revises: 0011_add_integration_outbox
Create Date: 2026-02-13 16:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012_add_guarantor_requests"
down_revision: str | None = "0011_add_integration_outbox"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'ASSIGN_GUARANTOR_REQUEST'")
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'REJECT_GUARANTOR_REQUEST'")

    guarantor_status_enum = postgresql.ENUM(
        "NEW",
        "ASSIGNED",
        "REJECTED",
        name="guarantor_request_status",
    )
    guarantor_status_enum.create(op.get_bind(), checkfirst=True)

    guarantor_status_column = postgresql.ENUM(
        "NEW",
        "ASSIGNED",
        "REJECTED",
        name="guarantor_request_status",
        create_type=False,
    )

    op.create_table(
        "guarantor_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "status",
            guarantor_status_column,
            server_default=sa.text("'NEW'"),
            nullable=False,
        ),
        sa.Column("submitter_user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_user_id", sa.BigInteger(), nullable=True),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("queue_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("queue_message_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["submitter_user_id"], ["users.id"], name=op.f("fk_guarantor_requests_submitter_user_id_users"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["moderator_user_id"], ["users.id"], name=op.f("fk_guarantor_requests_moderator_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guarantor_requests")),
    )
    op.create_index(
        "ix_guarantor_requests_status_created_at",
        "guarantor_requests",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_guarantor_requests_submitter_status",
        "guarantor_requests",
        ["submitter_user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_guarantor_requests_submitter_status", table_name="guarantor_requests")
    op.drop_index("ix_guarantor_requests_status_created_at", table_name="guarantor_requests")
    op.drop_table("guarantor_requests")

    op.execute("DROP TYPE IF EXISTS guarantor_request_status")

    # PostgreSQL enum value removal is intentionally unsupported for moderation_action.
