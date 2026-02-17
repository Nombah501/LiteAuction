"""add notification quiet hours timezone column

Revision ID: 0035_notif_quiet_tz
Revises: 0034_notif_quiet_hours
Create Date: 2026-02-18 00:05:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035_notif_quiet_tz"
down_revision: str | None = "0034_notif_quiet_hours"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "user_notification_preferences"
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}

    if "quiet_hours_timezone" not in existing_columns:
        op.add_column(
            table_name,
            sa.Column(
                "quiet_hours_timezone",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("'UTC'"),
            ),
        )

    op.execute(
        sa.text(
            """
            UPDATE user_notification_preferences
            SET quiet_hours_timezone = 'UTC'
            WHERE quiet_hours_timezone IS NULL OR quiet_hours_timezone = ''
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "user_notification_preferences"
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}

    if "quiet_hours_timezone" in existing_columns:
        op.drop_column(table_name, "quiet_hours_timezone")
