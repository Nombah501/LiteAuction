"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-02-11 15:10:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    user_role = postgresql.ENUM(
        "OWNER", "ADMIN", "MODERATOR", "SELLER", "BIDDER", name="user_role", create_type=False
    )
    auction_status = postgresql.ENUM(
        "DRAFT",
        "ACTIVE",
        "ENDED",
        "BOUGHT_OUT",
        "CANCELLED",
        "FROZEN",
        name="auction_status",
        create_type=False,
    )
    moderation_action = postgresql.ENUM(
        "FREEZE_AUCTION",
        "UNFREEZE_AUCTION",
        "END_AUCTION",
        "REMOVE_BID",
        "BAN_USER",
        "UNBAN_USER",
        name="moderation_action",
        create_type=False,
    )

    user_role.create(bind, checkfirst=True)
    auction_status.create(bind, checkfirst=True)
    moderation_action.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("is_notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.UniqueConstraint("tg_user_id", name="uq_users_tg_user_id"),
    )

    op.create_table(
        "user_roles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.UniqueConstraint("user_id", "role", name="uq_user_roles_user_id_role"),
    )

    op.create_table(
        "auctions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("seller_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("photo_file_id", sa.Text(), nullable=False),
        sa.Column("start_price", sa.Integer(), nullable=False),
        sa.Column("buyout_price", sa.Integer(), nullable=True),
        sa.Column("min_step", sa.Integer(), nullable=False),
        sa.Column("duration_hours", sa.SmallInteger(), nullable=False),
        sa.Column("anti_sniper_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("anti_sniper_extensions_used", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("anti_sniper_max_extensions", sa.SmallInteger(), nullable=False, server_default=sa.text("3")),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", auction_status, nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("winner_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.CheckConstraint("start_price >= 1", name="ck_auctions_start_price_positive"),
        sa.CheckConstraint("min_step >= 1", name="ck_auctions_min_step_positive"),
        sa.CheckConstraint(
            "buyout_price IS NULL OR buyout_price >= start_price",
            name="ck_auctions_buyout_gte_start",
        ),
        sa.CheckConstraint("duration_hours IN (6, 12, 18, 24)", name="ck_auctions_duration_options"),
    )

    op.create_index("ix_auctions_status_ends_at", "auctions", ["status", "ends_at"], unique=False)

    op.create_table(
        "bids",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("auction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("is_removed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("removed_reason", sa.Text(), nullable=True),
        sa.Column("removed_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.CheckConstraint("amount >= 1", name="ck_bids_amount_positive"),
    )

    op.create_index("ix_bids_auction_created_at", "bids", ["auction_id", "created_at"], unique=False)
    op.create_index("ix_bids_auction_amount", "bids", ["auction_id", "amount"], unique=False)

    op.create_table(
        "auction_posts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("auction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("auctions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("inline_message_id", sa.Text(), nullable=True),
        sa.Column("published_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.UniqueConstraint("auction_id", "chat_id", "message_id", name="uq_auction_posts_message"),
        sa.UniqueConstraint("inline_message_id", name="uq_auction_posts_inline_message_id"),
    )

    op.create_table(
        "blacklist_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("user_id", name="uq_blacklist_entries_user_id"),
    )

    op.create_table(
        "moderation_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("auctions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("bid_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bids.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", moderation_action, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("TIMEZONE('utc', NOW())")),
    )

    op.create_index("ix_moderation_logs_created_at", "moderation_logs", ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("ix_moderation_logs_created_at", table_name="moderation_logs")
    op.drop_table("moderation_logs")
    op.drop_table("blacklist_entries")
    op.drop_table("auction_posts")
    op.drop_index("ix_bids_auction_amount", table_name="bids")
    op.drop_index("ix_bids_auction_created_at", table_name="bids")
    op.drop_table("bids")
    op.drop_index("ix_auctions_status_ends_at", table_name="auctions")
    op.drop_table("auctions")
    op.drop_table("user_roles")
    op.drop_table("users")

    moderation_action = postgresql.ENUM(name="moderation_action")
    auction_status = postgresql.ENUM(name="auction_status")
    user_role = postgresql.ENUM(name="user_role")

    moderation_action.drop(bind, checkfirst=True)
    auction_status.drop(bind, checkfirst=True)
    user_role.drop(bind, checkfirst=True)
