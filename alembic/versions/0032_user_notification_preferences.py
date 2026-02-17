"""add user notification preferences table

Revision ID: 0032_user_notif_prefs
Revises: 0031_runtime_setting_overrides
Create Date: 2026-02-17 16:45:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032_user_notif_prefs"
down_revision: str | None = "0031_runtime_setting_overrides"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_notification_preferences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("preset", sa.String(length=16), nullable=False, server_default=sa.text("'recommended'")),
        sa.Column("outbid_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auction_finish_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auction_win_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("auction_mod_actions_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("points_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("support_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("TIMEZONE('utc', NOW())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("TIMEZONE('utc', NOW())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "preset IN ('recommended', 'important', 'all', 'custom')",
            name="ck_user_notification_preferences_preset",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_notification_preferences_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_notification_preferences"),
        sa.UniqueConstraint("user_id", name="uq_user_notification_preferences_user_id"),
    )
    op.create_index(
        "ix_user_notification_preferences_user_id",
        "user_notification_preferences",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_notification_preferences_updated_at",
        "user_notification_preferences",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_notification_preferences_updated_at", table_name="user_notification_preferences")
    op.drop_index("ix_user_notification_preferences_user_id", table_name="user_notification_preferences")
    op.drop_table("user_notification_preferences")
