"""add feedback priority boost columns

Revision ID: 0018_feedback_boost_cols
Revises: 0017_points_boost_evt
Create Date: 2026-02-14 17:12:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_feedback_boost_cols"
down_revision: str | None = "0017_points_boost_evt"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "feedback_items",
        sa.Column("priority_boost_points_spent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "feedback_items",
        sa.Column("priority_boosted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_feedback_items_priority_boosted_at", "feedback_items", ["priority_boosted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feedback_items_priority_boosted_at", table_name="feedback_items")
    op.drop_column("feedback_items", "priority_boosted_at")
    op.drop_column("feedback_items", "priority_boost_points_spent")
