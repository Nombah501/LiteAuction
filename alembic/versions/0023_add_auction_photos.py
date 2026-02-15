"""add auction photos table

Revision ID: 0023_add_auction_photos
Revises: 0022_appeal_boost_cols
Create Date: 2026-02-15 13:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0023_add_auction_photos"
down_revision: str | None = "0022_appeal_boost_cols"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auction_photos",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("auction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(["auction_id"], ["auctions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("auction_id", "position", name="uq_auction_photos_auction_position"),
    )
    op.create_index("ix_auction_photos_auction_id", "auction_photos", ["auction_id"], unique=False)

    op.execute(
        """
        INSERT INTO auction_photos (auction_id, file_id, position)
        SELECT id, photo_file_id, 0
        FROM auctions
        WHERE photo_file_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_auction_photos_auction_id", table_name="auction_photos")
    op.drop_table("auction_photos")
