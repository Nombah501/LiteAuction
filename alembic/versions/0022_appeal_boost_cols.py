"""add appeal priority boost columns

Revision ID: 0022_appeal_boost_cols
Revises: 0021_points_appeal_boost_evt
Create Date: 2026-02-14 20:46:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_appeal_boost_cols"
down_revision: str | None = "0021_points_appeal_boost_evt"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "appeals",
        sa.Column("priority_boost_points_spent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "appeals",
        sa.Column("priority_boosted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_appeals_priority_boosted_at", "appeals", ["priority_boosted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_appeals_priority_boosted_at", table_name="appeals")
    op.drop_column("appeals", "priority_boosted_at")
    op.drop_column("appeals", "priority_boost_points_spent")
