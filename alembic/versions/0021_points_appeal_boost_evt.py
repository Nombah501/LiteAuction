"""add points event for appeal boost

Revision ID: 0021_points_appeal_boost_evt
Revises: 0020_guarantor_boost_cols
Create Date: 2026-02-14 20:45:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_points_appeal_boost_evt"
down_revision: str | None = "0020_guarantor_boost_cols"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE points_event_type ADD VALUE IF NOT EXISTS 'APPEAL_PRIORITY_BOOST'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported.
    pass
