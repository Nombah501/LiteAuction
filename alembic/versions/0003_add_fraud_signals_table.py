"""add fraud signals table

Revision ID: 0003_add_fraud_signals_table
Revises: 0002_add_complaints_table
Create Date: 2026-02-11 16:45:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_add_fraud_signals_table"
down_revision: str | None = "0002_add_complaints_table"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fraud_signals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "auction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("auctions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bid_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bids.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("queue_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("queue_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("TIMEZONE('utc', NOW())"),
        ),
        sa.CheckConstraint("score >= 1", name="ck_fraud_signals_score_positive"),
        sa.CheckConstraint(
            "status IN ('OPEN', 'CONFIRMED', 'DISMISSED')",
            name="ck_fraud_signals_status_values",
        ),
    )

    op.create_index("ix_fraud_signals_auction_id", "fraud_signals", ["auction_id"], unique=False)
    op.create_index("ix_fraud_signals_user_id", "fraud_signals", ["user_id"], unique=False)
    op.create_index("ix_fraud_signals_status", "fraud_signals", ["status"], unique=False)
    op.create_index("ix_fraud_signals_created_at", "fraud_signals", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_fraud_signals_created_at", table_name="fraud_signals")
    op.drop_index("ix_fraud_signals_status", table_name="fraud_signals")
    op.drop_index("ix_fraud_signals_user_id", table_name="fraud_signals")
    op.drop_index("ix_fraud_signals_auction_id", table_name="fraud_signals")
    op.drop_table("fraud_signals")
