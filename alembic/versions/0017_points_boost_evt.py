"""add points event for feedback boost

Revision ID: 0017_points_boost_evt
Revises: 0016_trade_feedback_actions
Create Date: 2026-02-14 17:10:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_points_boost_evt"
down_revision: str | None = "0016_trade_feedback_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE points_event_type ADD VALUE IF NOT EXISTS 'FEEDBACK_PRIORITY_BOOST'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported.
    pass
