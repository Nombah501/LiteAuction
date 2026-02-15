"""add moderation checklist task replies

Revision ID: 0027_checklist_replies
Revises: 0026_moderation_checklists
Create Date: 2026-02-16 01:05:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_checklist_replies"
down_revision: str | None = "0026_moderation_checklists"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "moderation_checklist_replies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("checklist_item_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.ForeignKeyConstraint(["checklist_item_id"], ["moderation_checklist_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_moderation_checklist_replies_checklist_item_id",
        "moderation_checklist_replies",
        ["checklist_item_id"],
    )
    op.create_index(
        "ix_moderation_checklist_replies_created_at",
        "moderation_checklist_replies",
        ["created_at"],
    )
    op.create_index(
        "ix_moderation_checklist_replies_item_created",
        "moderation_checklist_replies",
        ["checklist_item_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_moderation_checklist_replies_item_created", table_name="moderation_checklist_replies")
    op.drop_index("ix_moderation_checklist_replies_created_at", table_name="moderation_checklist_replies")
    op.drop_index("ix_moderation_checklist_replies_checklist_item_id", table_name="moderation_checklist_replies")
    op.drop_table("moderation_checklist_replies")
