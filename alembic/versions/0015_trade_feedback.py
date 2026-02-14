"""add post trade feedback table

Revision ID: 0015_trade_feedback
Revises: 0014_adjust_user_points_action
Create Date: 2026-02-14 16:10:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015_trade_feedback"
down_revision: str | None = "0014_adjust_user_points_action"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_feedback",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("auction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'VISIBLE'"), nullable=False),
        sa.Column("moderator_user_id", sa.BigInteger(), nullable=True),
        sa.Column("moderation_note", sa.Text(), nullable=True),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="trade_feedback_rating_range"),
        sa.CheckConstraint("status IN ('VISIBLE', 'HIDDEN')", name="trade_feedback_status_values"),
        sa.CheckConstraint("author_user_id <> target_user_id", name="trade_feedback_distinct_users"),
        sa.ForeignKeyConstraint(["auction_id"], ["auctions.id"], name=op.f("fk_trade_feedback_auction_id_auctions"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            name=op.f("fk_trade_feedback_author_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            name=op.f("fk_trade_feedback_target_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["moderator_user_id"],
            ["users.id"],
            name=op.f("fk_trade_feedback_moderator_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trade_feedback")),
        sa.UniqueConstraint("auction_id", "author_user_id", name="uq_trade_feedback_auction_author"),
    )
    op.create_index("ix_trade_feedback_status_created_at", "trade_feedback", ["status", "created_at"], unique=False)
    op.create_index("ix_trade_feedback_target_created_at", "trade_feedback", ["target_user_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_trade_feedback_target_created_at", table_name="trade_feedback")
    op.drop_index("ix_trade_feedback_status_created_at", table_name="trade_feedback")
    op.drop_table("trade_feedback")
