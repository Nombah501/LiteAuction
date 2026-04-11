"""user_reputations

Revision ID: 0039_user_reputations
Revises: 0038_workflow_preset_telemetry
"""
from alembic import op
import sqlalchemy as sa


revision = "0039_user_reputations"
down_revision = "0038_workflow_preset_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_reputation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("TIMEZONE('utc', NOW())"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reputation_events_user_created",
        "user_reputation_events",
        ["user_id", "created_at"],
    )
    op.create_foreign_key(
        "fk_user_reputation_events_user_id",
        "user_reputation_events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_table(
        "user_reputations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier", sa.String(16), nullable=False, server_default="NEW"),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_foreign_key(
        "fk_user_reputations_user_id",
        "user_reputations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_table("user_reputations")
    op.drop_table("user_reputation_events")
