"""add escalate appeal moderation action

Revision ID: 0009_add_escalate_appeal_action
Revises: 0008_add_appeal_sla_fields
Create Date: 2026-02-13 06:55:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_add_escalate_appeal_action"
down_revision: str | None = "0008_add_appeal_sla_fields"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'ESCALATE_APPEAL'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported in downgrade path.
    pass
