"""add points event for guarantor boost

Revision ID: 0019_points_guarantor_boost_evt
Revises: 0018_feedback_boost_cols
Create Date: 2026-02-14 18:15:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_points_guarantor_boost_evt"
down_revision: str | None = "0018_feedback_boost_cols"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE points_event_type ADD VALUE IF NOT EXISTS 'GUARANTOR_PRIORITY_BOOST'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported.
    pass
