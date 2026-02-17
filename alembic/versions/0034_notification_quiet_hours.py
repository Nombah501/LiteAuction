"""add notification quiet hours columns

Revision ID: 0034_notif_quiet_hours
Revises: 0033_user_auc_notif_snooze
Create Date: 2026-02-17 20:35:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034_notif_quiet_hours"
down_revision: str | None = "0033_user_auc_notif_snooze"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_notification_preferences",
        sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "user_notification_preferences",
        sa.Column("quiet_hours_start_hour", sa.SmallInteger(), nullable=False, server_default=sa.text("23")),
    )
    op.add_column(
        "user_notification_preferences",
        sa.Column("quiet_hours_end_hour", sa.SmallInteger(), nullable=False, server_default=sa.text("8")),
    )
    op.create_check_constraint(
        "ck_user_notification_preferences_quiet_start_hour",
        "user_notification_preferences",
        "quiet_hours_start_hour >= 0 AND quiet_hours_start_hour <= 23",
    )
    op.create_check_constraint(
        "ck_user_notification_preferences_quiet_end_hour",
        "user_notification_preferences",
        "quiet_hours_end_hour >= 0 AND quiet_hours_end_hour <= 23",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_notification_preferences_quiet_end_hour",
        "user_notification_preferences",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_notification_preferences_quiet_start_hour",
        "user_notification_preferences",
        type_="check",
    )
    op.drop_column("user_notification_preferences", "quiet_hours_end_hour")
    op.drop_column("user_notification_preferences", "quiet_hours_start_hour")
    op.drop_column("user_notification_preferences", "quiet_hours_enabled")
