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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "user_auction_notification_snoozes"

    if table_name not in inspector.get_table_names():
        op.create_table(
            table_name,
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

    indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    if "ix_user_auction_notification_snoozes_user_id" not in indexes:
        op.create_index(
            "ix_user_auction_notification_snoozes_user_id",
            table_name,
            ["user_id"],
            unique=False,
        )
    if "ix_user_auction_notification_snoozes_expires_at" not in indexes:
        op.create_index(
            "ix_user_auction_notification_snoozes_expires_at",
            table_name,
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_name = "user_auction_notification_snoozes"
    if table_name not in inspector.get_table_names():
        return

    indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    if "ix_user_auction_notification_snoozes_expires_at" in indexes:
        op.drop_index(
            "ix_user_auction_notification_snoozes_expires_at",
            table_name=table_name,
        )
    if "ix_user_auction_notification_snoozes_user_id" in indexes:
        op.drop_index(
            "ix_user_auction_notification_snoozes_user_id",
            table_name=table_name,
        )
    op.drop_table(table_name)
