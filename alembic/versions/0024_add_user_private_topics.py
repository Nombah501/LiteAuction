"""add user private topics table

Revision ID: 0024_add_user_private_topics
Revises: 0023_add_auction_photos
Create Date: 2026-02-15 21:35:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_add_user_private_topics"
down_revision: str | None = "0023_add_auction_photos"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_private_topics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "purpose", name="uq_user_private_topics_user_purpose"),
        sa.UniqueConstraint("user_id", "thread_id", name="uq_user_private_topics_user_thread"),
    )
    op.create_index("ix_user_private_topics_user_id", "user_private_topics", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_private_topics_user_id", table_name="user_private_topics")
    op.drop_table("user_private_topics")
