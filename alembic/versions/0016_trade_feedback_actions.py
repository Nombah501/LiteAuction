"""add trade feedback moderation actions

Revision ID: 0016_trade_feedback_actions
Revises: 0015_trade_feedback
Create Date: 2026-02-14 16:45:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_trade_feedback_actions"
down_revision: str | None = "0015_trade_feedback"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'HIDE_TRADE_FEEDBACK'")
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'UNHIDE_TRADE_FEEDBACK'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported.
    pass
