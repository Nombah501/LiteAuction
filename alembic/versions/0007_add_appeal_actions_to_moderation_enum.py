"""add appeal moderation actions

Revision ID: 0007_appeal_mod_actions
Revises: 0006_add_appeals_table
Create Date: 2026-02-13 00:55:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_appeal_mod_actions"
down_revision: str | None = "0006_add_appeals_table"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'RESOLVE_APPEAL'")
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'REJECT_APPEAL'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported in downgrade path.
    pass
