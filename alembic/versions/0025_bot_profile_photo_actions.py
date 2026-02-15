"""add bot profile photo moderation actions

Revision ID: 0025_bot_profile_photo_actions
Revises: 0024_add_user_private_topics
Create Date: 2026-02-16 09:30:00
"""

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025_bot_profile_photo_actions"
down_revision: str | None = "0024_add_user_private_topics"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'SET_BOT_PROFILE_PHOTO'")
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'REMOVE_BOT_PROFILE_PHOTO'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally unsupported.
    pass
