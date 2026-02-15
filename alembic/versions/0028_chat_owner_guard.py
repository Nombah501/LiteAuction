"""add chat owner service event audit

Revision ID: 0028_chat_owner_guard
Revises: 0027_checklist_replies
Create Date: 2026-02-16 10:20:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0028_chat_owner_guard"
down_revision: str | None = "0027_checklist_replies"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_owner_service_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("old_owner_tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("new_owner_tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("requires_confirmation", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("resolved_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "message_id", "event_type", name="uq_chat_owner_service_events_message"),
    )
    op.create_index("ix_chat_owner_service_events_chat_id", "chat_owner_service_events", ["chat_id"])
    op.create_index("ix_chat_owner_service_events_created_at", "chat_owner_service_events", ["created_at"])
    op.create_index("ix_chat_owner_service_events_requires_confirmation", "chat_owner_service_events", ["requires_confirmation"])
    op.create_index("ix_chat_owner_service_events_resolved_at", "chat_owner_service_events", ["resolved_at"])
    op.create_index(
        "ix_chat_owner_service_events_chat_pending",
        "chat_owner_service_events",
        ["chat_id", "requires_confirmation", "resolved_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_owner_service_events_chat_pending", table_name="chat_owner_service_events")
    op.drop_index("ix_chat_owner_service_events_resolved_at", table_name="chat_owner_service_events")
    op.drop_index("ix_chat_owner_service_events_requires_confirmation", table_name="chat_owner_service_events")
    op.drop_index("ix_chat_owner_service_events_created_at", table_name="chat_owner_service_events")
    op.drop_index("ix_chat_owner_service_events_chat_id", table_name="chat_owner_service_events")
    op.drop_table("chat_owner_service_events")
