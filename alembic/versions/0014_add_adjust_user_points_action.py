"""add adjust user points moderation action

Revision ID: 0014_add_adjust_user_points_action
Revises: 0013_add_points_ledger
Create Date: 2026-02-13 20:00:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_add_adjust_user_points_action"
down_revision: str | None = "0013_add_points_ledger"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'ADJUST_USER_POINTS'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported.
    pass
