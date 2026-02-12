"""add feedback items table and moderation actions

Revision ID: 0010_add_feedback_items
Revises: 0009_add_escalate_appeal_action
Create Date: 2026-02-13 08:30:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_add_feedback_items"
down_revision: str | None = "0009_add_escalate_appeal_action"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'TAKE_FEEDBACK'")
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'APPROVE_FEEDBACK'")
    op.execute("ALTER TYPE moderation_action ADD VALUE IF NOT EXISTS 'REJECT_FEEDBACK'")

    feedback_type = postgresql.ENUM("BUG", "SUGGESTION", name="feedback_type")
    feedback_status = postgresql.ENUM("NEW", "IN_REVIEW", "APPROVED", "REJECTED", name="feedback_status")
    feedback_type.create(op.get_bind(), checkfirst=True)
    feedback_status.create(op.get_bind(), checkfirst=True)

    feedback_type_column = postgresql.ENUM(
        "BUG",
        "SUGGESTION",
        name="feedback_type",
        create_type=False,
    )
    feedback_status_column = postgresql.ENUM(
        "NEW",
        "IN_REVIEW",
        "APPROVED",
        "REJECTED",
        name="feedback_status",
        create_type=False,
    )

    op.create_table(
        "feedback_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("type", feedback_type_column, nullable=False),
        sa.Column(
            "status",
            feedback_status_column,
            nullable=False,
            server_default=sa.text("'NEW'"),
        ),
        sa.Column("submitter_user_id", sa.BigInteger(), nullable=False),
        sa.Column("moderator_user_id", sa.BigInteger(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("reward_points", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("queue_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("queue_message_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("github_issue_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("TIMEZONE('utc', NOW())"), nullable=False),
        sa.ForeignKeyConstraint(["moderator_user_id"], ["users.id"], name=op.f("fk_feedback_items_moderator_user_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitter_user_id"], ["users.id"], name=op.f("fk_feedback_items_submitter_user_id_users"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback_items")),
    )
    op.create_index(
        "ix_feedback_items_type_status_created_at",
        "feedback_items",
        ["type", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_feedback_items_submitter_status",
        "feedback_items",
        ["submitter_user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_items_submitter_status", table_name="feedback_items")
    op.drop_index("ix_feedback_items_type_status_created_at", table_name="feedback_items")
    op.drop_table("feedback_items")

    op.execute("DROP TYPE IF EXISTS feedback_status")
    op.execute("DROP TYPE IF EXISTS feedback_type")

    # PostgreSQL enum value removal is intentionally unsupported for moderation_action.
