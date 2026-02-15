"""add moderation checklist items

Revision ID: 0026_add_moderation_checklist_items
Revises: 0025_add_suggested_post_reviews
Create Date: 2026-02-16 00:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026_add_moderation_checklist_items"
down_revision: str | None = "0025_add_suggested_post_reviews"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'UPDATE_MODERATION_CHECKLIST'")

    op.create_table(
        "moderation_checklist_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("item_label", sa.String(length=255), nullable=False),
        sa.Column("is_done", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("done_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.ForeignKeyConstraint(["done_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_type", "entity_id", "item_code", name="uq_moderation_checklist_item_scope"),
    )
    op.create_index("ix_moderation_checklist_entity", "moderation_checklist_items", ["entity_type", "entity_id"])
    op.create_index("ix_moderation_checklist_items_is_done", "moderation_checklist_items", ["is_done"])


def downgrade() -> None:
    op.drop_index("ix_moderation_checklist_items_is_done", table_name="moderation_checklist_items")
    op.drop_index("ix_moderation_checklist_entity", table_name="moderation_checklist_items")
    op.drop_table("moderation_checklist_items")
