"""add complaints table

Revision ID: 0002_add_complaints_table
Revises: 0001_initial_schema
Create Date: 2026-02-11 16:20:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_add_complaints_table"
down_revision: str | None = "0001_initial_schema"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "complaints",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "auction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("auctions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reporter_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "target_bid_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bids.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED', 'DISMISSED')",
            name="ck_complaints_status_values",
        ),
    )

    op.create_index("ix_complaints_auction_id", "complaints", ["auction_id"], unique=False)
    op.create_index("ix_complaints_reporter_user_id", "complaints", ["reporter_user_id"], unique=False)
    op.create_index("ix_complaints_status", "complaints", ["status"], unique=False)
    op.create_index("ix_complaints_created_at", "complaints", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_complaints_created_at", table_name="complaints")
    op.drop_index("ix_complaints_status", table_name="complaints")
    op.drop_index("ix_complaints_reporter_user_id", table_name="complaints")
    op.drop_index("ix_complaints_auction_id", table_name="complaints")
    op.drop_table("complaints")
