"""add_deal_topics_and_guarantor_auction_id

Revision ID: d6ae79c2bdc0
Revises: 0039_user_reputations
Create Date: 2026-04-11 13:47:53.333670

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6ae79c2bdc0"
down_revision: Union[str, None] = "0039_user_reputations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deal_topics",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "auction_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("auctions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seller_topic_id", sa.BigInteger(), nullable=False),
        sa.Column("winner_topic_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "CLOSED", name="deal_topic_status"),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index("ix_deal_topics_auction_id", "deal_topics", ["auction_id"])
    op.create_index("ix_deal_topics_status", "deal_topics", ["status"])

    op.add_column(
        "guarantor_requests",
        sa.Column(
            "auction_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("auctions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("guarantor_requests", "auction_id")
    op.drop_index("ix_deal_topics_status", table_name="deal_topics")
    op.drop_index("ix_deal_topics_auction_id", table_name="deal_topics")
    op.drop_table("deal_topics")

    op.execute("DROP TYPE IF EXISTS deal_topic_status")
