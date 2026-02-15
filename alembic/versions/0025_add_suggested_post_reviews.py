"""add suggested post reviews table

Revision ID: 0025_add_suggested_post_reviews
Revises: 0024_add_user_private_topics
Create Date: 2026-02-15 23:05:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0025_add_suggested_post_reviews"
down_revision: str | None = "0024_add_user_private_topics"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suggested_post_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("source_direct_messages_topic_id", sa.BigInteger(), nullable=True),
        sa.Column("submitter_user_id", sa.BigInteger(), nullable=True),
        sa.Column("submitter_tg_user_id", sa.BigInteger(), nullable=True),
        sa.Column("queue_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("queue_message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("TIMEZONE('utc', NOW())"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("TIMEZONE('utc', NOW())"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'DECLINED', 'FAILED')",
            name="suggested_post_reviews_status_values",
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitter_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_chat_id",
            "source_message_id",
            name="uq_suggested_post_reviews_source",
        ),
    )
    op.create_index(
        "ix_suggested_post_reviews_status",
        "suggested_post_reviews",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_suggested_post_reviews_status_created_at",
        "suggested_post_reviews",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_suggested_post_reviews_status_created_at", table_name="suggested_post_reviews")
    op.drop_index("ix_suggested_post_reviews_status", table_name="suggested_post_reviews")
    op.drop_table("suggested_post_reviews")
