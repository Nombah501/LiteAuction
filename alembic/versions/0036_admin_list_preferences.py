"""add admin list preferences table

Revision ID: 0036_admin_list_preferences
Revises: 0035_notif_quiet_tz
Create Date: 2026-02-19 00:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036_admin_list_preferences"
down_revision: str | None = "0035_notif_quiet_tz"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "admin_list_preferences"

    if not inspector.has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("subject_key", sa.String(length=128), nullable=False),
            sa.Column("queue_key", sa.String(length=64), nullable=False),
            sa.Column(
                "density",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'standard'"),
            ),
            sa.Column(
                "columns_json",
                sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
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
                "density IN ('compact', 'standard', 'comfortable')",
                name="ck_admin_list_preferences_admin_list_preferences_density_values",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_admin_list_preferences"),
            sa.UniqueConstraint(
                "subject_key",
                "queue_key",
                name="uq_admin_list_preferences_subject_queue",
            ),
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
    if "ix_admin_list_preferences_subject_key" not in existing_indexes:
        op.create_index(
            "ix_admin_list_preferences_subject_key",
            table_name,
            ["subject_key"],
            unique=False,
        )
    if "ix_admin_list_preferences_queue_key" not in existing_indexes:
        op.create_index(
            "ix_admin_list_preferences_queue_key",
            table_name,
            ["queue_key"],
            unique=False,
        )
    if "ix_admin_list_preferences_updated_at" not in existing_indexes:
        op.create_index(
            "ix_admin_list_preferences_updated_at",
            table_name,
            ["updated_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "admin_list_preferences"

    if inspector.has_table(table_name):
        existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
        if "ix_admin_list_preferences_updated_at" in existing_indexes:
            op.drop_index("ix_admin_list_preferences_updated_at", table_name=table_name)
        if "ix_admin_list_preferences_queue_key" in existing_indexes:
            op.drop_index("ix_admin_list_preferences_queue_key", table_name=table_name)
        if "ix_admin_list_preferences_subject_key" in existing_indexes:
            op.drop_index("ix_admin_list_preferences_subject_key", table_name=table_name)
        op.drop_table(table_name)
