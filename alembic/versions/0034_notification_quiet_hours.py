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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "user_notification_preferences"
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}

    if "quiet_hours_enabled" not in existing_columns:
        op.add_column(
            table_name,
            sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    if "quiet_hours_start_hour" not in existing_columns:
        op.add_column(
            table_name,
            sa.Column("quiet_hours_start_hour", sa.SmallInteger(), nullable=False, server_default=sa.text("23")),
        )
    if "quiet_hours_end_hour" not in existing_columns:
        op.add_column(
            table_name,
            sa.Column("quiet_hours_end_hour", sa.SmallInteger(), nullable=False, server_default=sa.text("8")),
        )

    checks = {check["name"] for check in inspector.get_check_constraints(table_name)}
    if "ck_user_notification_preferences_quiet_start_hour" not in checks:
        op.create_check_constraint(
            "ck_user_notification_preferences_quiet_start_hour",
            table_name,
            "quiet_hours_start_hour >= 0 AND quiet_hours_start_hour <= 23",
        )
    if "ck_user_notification_preferences_quiet_end_hour" not in checks:
        op.create_check_constraint(
            "ck_user_notification_preferences_quiet_end_hour",
            table_name,
            "quiet_hours_end_hour >= 0 AND quiet_hours_end_hour <= 23",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "user_notification_preferences"
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    checks = {check["name"] for check in inspector.get_check_constraints(table_name)}

    if "ck_user_notification_preferences_quiet_end_hour" in checks:
        op.drop_constraint(
            "ck_user_notification_preferences_quiet_end_hour",
            table_name,
            type_="check",
        )
    if "ck_user_notification_preferences_quiet_start_hour" in checks:
        op.drop_constraint(
            "ck_user_notification_preferences_quiet_start_hour",
            table_name,
            type_="check",
        )
    if "quiet_hours_end_hour" in existing_columns:
        op.drop_column(table_name, "quiet_hours_end_hour")
    if "quiet_hours_start_hour" in existing_columns:
        op.drop_column(table_name, "quiet_hours_start_hour")
    if "quiet_hours_enabled" in existing_columns:
        op.drop_column(table_name, "quiet_hours_enabled")
