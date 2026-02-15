"""add runtime setting overrides table

Revision ID: 0031_runtime_setting_overrides
Revises: 0030_bot_profile_photo_actions
Create Date: 2026-02-16 02:00:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031_runtime_setting_overrides"
down_revision: str | None = "0030_bot_profile_photo_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_setting_overrides",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint("char_length(key) > 0", name="ck_runtime_setting_overrides_runtime_setting_overrides_key_not_blank"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_runtime_setting_overrides_updated_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_runtime_setting_overrides"),
        sa.UniqueConstraint("key", name="uq_runtime_setting_overrides_key"),
    )
    op.create_index(
        "ix_runtime_setting_overrides_key",
        "runtime_setting_overrides",
        ["key"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_setting_overrides_updated_at",
        "runtime_setting_overrides",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_setting_overrides_updated_by_user_id",
        "runtime_setting_overrides",
        ["updated_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_setting_overrides_updated_by_user_id", table_name="runtime_setting_overrides")
    op.drop_index("ix_runtime_setting_overrides_updated_at", table_name="runtime_setting_overrides")
    op.drop_index("ix_runtime_setting_overrides_key", table_name="runtime_setting_overrides")
    op.drop_table("runtime_setting_overrides")
