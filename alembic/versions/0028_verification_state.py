"""add telegram verification state tables

Revision ID: 0028_verification_state
Revises: 0027_checklist_replies
Create Date: 2026-02-16 11:25:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028_verification_state"
down_revision: str | None = "0027_checklist_replies"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_user_verifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("custom_description", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tg_user_id", name="uq_telegram_user_verifications_tg_user_id"),
    )
    op.create_index("ix_telegram_user_verifications_tg_user_id", "telegram_user_verifications", ["tg_user_id"])
    op.create_index("ix_telegram_user_verifications_is_verified", "telegram_user_verifications", ["is_verified"])
    op.create_index("ix_telegram_user_verifications_updated_at", "telegram_user_verifications", ["updated_at"])

    op.create_table(
        "telegram_chat_verifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("custom_description", sa.Text(), nullable=True),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", name="uq_telegram_chat_verifications_chat_id"),
    )
    op.create_index("ix_telegram_chat_verifications_chat_id", "telegram_chat_verifications", ["chat_id"])
    op.create_index("ix_telegram_chat_verifications_is_verified", "telegram_chat_verifications", ["is_verified"])
    op.create_index("ix_telegram_chat_verifications_updated_at", "telegram_chat_verifications", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_telegram_chat_verifications_updated_at", table_name="telegram_chat_verifications")
    op.drop_index("ix_telegram_chat_verifications_is_verified", table_name="telegram_chat_verifications")
    op.drop_index("ix_telegram_chat_verifications_chat_id", table_name="telegram_chat_verifications")
    op.drop_table("telegram_chat_verifications")

    op.drop_index("ix_telegram_user_verifications_updated_at", table_name="telegram_user_verifications")
    op.drop_index("ix_telegram_user_verifications_is_verified", table_name="telegram_user_verifications")
    op.drop_index("ix_telegram_user_verifications_tg_user_id", table_name="telegram_user_verifications")
    op.drop_table("telegram_user_verifications")
