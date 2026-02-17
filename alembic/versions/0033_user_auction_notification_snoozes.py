"""add user auction notification snoozes table

Revision ID: 0033_user_auc_notif_snooze
Revises: 0032_user_notif_prefs
Create Date: 2026-02-17 20:25:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033_user_auc_notif_snooze"
down_revision: str | None = "0032_user_notif_prefs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_auction_notification_snoozes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("auction_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_auction_notification_snoozes_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_auction_notification_snoozes"),
        sa.UniqueConstraint(
            "user_id",
            "auction_id",
            name="uq_user_auction_notification_snoozes_user_auction",
        ),
    )
    op.create_index(
        "ix_user_auction_notification_snoozes_user_id",
        "user_auction_notification_snoozes",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_auction_notification_snoozes_expires_at",
        "user_auction_notification_snoozes",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_auction_notification_snoozes_expires_at",
        table_name="user_auction_notification_snoozes",
    )
    op.drop_index(
        "ix_user_auction_notification_snoozes_user_id",
        table_name="user_auction_notification_snoozes",
    )
    op.drop_table("user_auction_notification_snoozes")
