"""add workflow preset telemetry events

Revision ID: 0038_workflow_preset_telemetry
Revises: 0037_workflow_presets
Create Date: 2026-02-20 11:05:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038_workflow_preset_telemetry"
down_revision: str | None = "0037_workflow_presets"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("admin_queue_preset_telemetry_events"):
        op.create_table(
            "admin_queue_preset_telemetry_events",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("queue_context", sa.String(length=32), nullable=False),
            sa.Column("queue_key", sa.String(length=32), nullable=False),
            sa.Column("preset_id", sa.BigInteger(), nullable=True),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("actor_subject_key", sa.String(length=128), nullable=False),
            sa.Column("time_to_action_ms", sa.Integer(), nullable=True),
            sa.Column("reopen_signal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("filter_churn_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("TIMEZONE('utc', NOW())"),
                nullable=False,
            ),
            sa.CheckConstraint(
                "queue_context IN ('moderation', 'appeals', 'risk', 'feedback')",
                name="ck_admin_queue_preset_telemetry_events_admin_queue_preset_telemetry_events_queue_context_values",
            ),
            sa.CheckConstraint(
                "queue_key IN ('complaints', 'appeals', 'signals', 'trade_feedback')",
                name="ck_admin_queue_preset_telemetry_events_admin_queue_preset_telemetry_events_queue_key_values",
            ),
            sa.CheckConstraint(
                "action IN ('save', 'update', 'select', 'delete', 'set_default')",
                name="ck_admin_queue_preset_telemetry_events_admin_queue_preset_telemetry_events_action_values",
            ),
            sa.CheckConstraint(
                "time_to_action_ms IS NULL OR (time_to_action_ms >= 0 AND time_to_action_ms <= 86400000)",
                name="ck_admin_queue_preset_telemetry_events_admin_queue_preset_telemetry_events_time_to_action_ms_bounds",
            ),
            sa.CheckConstraint(
                "filter_churn_count >= 0 AND filter_churn_count <= 1000",
                name="ck_admin_queue_preset_telemetry_events_admin_queue_preset_telemetry_events_filter_churn_count_bounds",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_admin_queue_preset_telemetry_events"),
        )

    index_names = {
        index["name"] for index in inspector.get_indexes("admin_queue_preset_telemetry_events")
    }
    if "ix_admin_queue_preset_telemetry_events_queue_context" not in index_names:
        op.create_index(
            "ix_admin_queue_preset_telemetry_events_queue_context",
            "admin_queue_preset_telemetry_events",
            ["queue_context"],
            unique=False,
        )
    if "ix_admin_queue_preset_telemetry_events_queue_key" not in index_names:
        op.create_index(
            "ix_admin_queue_preset_telemetry_events_queue_key",
            "admin_queue_preset_telemetry_events",
            ["queue_key"],
            unique=False,
        )
    if "ix_admin_queue_preset_telemetry_events_preset_id" not in index_names:
        op.create_index(
            "ix_admin_queue_preset_telemetry_events_preset_id",
            "admin_queue_preset_telemetry_events",
            ["preset_id"],
            unique=False,
        )
    if "ix_admin_queue_preset_telemetry_events_action" not in index_names:
        op.create_index(
            "ix_admin_queue_preset_telemetry_events_action",
            "admin_queue_preset_telemetry_events",
            ["action"],
            unique=False,
        )
    if "ix_admin_queue_preset_telemetry_events_created_at" not in index_names:
        op.create_index(
            "ix_admin_queue_preset_telemetry_events_created_at",
            "admin_queue_preset_telemetry_events",
            ["created_at"],
            unique=False,
        )
    if "ix_admin_queue_preset_telemetry_events_queue_preset_created" not in index_names:
        op.create_index(
            "ix_admin_queue_preset_telemetry_events_queue_preset_created",
            "admin_queue_preset_telemetry_events",
            ["queue_key", "preset_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("admin_queue_preset_telemetry_events"):
        index_names = {
            index["name"] for index in inspector.get_indexes("admin_queue_preset_telemetry_events")
        }
        if "ix_admin_queue_preset_telemetry_events_queue_preset_created" in index_names:
            op.drop_index(
                "ix_admin_queue_preset_telemetry_events_queue_preset_created",
                table_name="admin_queue_preset_telemetry_events",
            )
        if "ix_admin_queue_preset_telemetry_events_created_at" in index_names:
            op.drop_index(
                "ix_admin_queue_preset_telemetry_events_created_at",
                table_name="admin_queue_preset_telemetry_events",
            )
        if "ix_admin_queue_preset_telemetry_events_action" in index_names:
            op.drop_index(
                "ix_admin_queue_preset_telemetry_events_action",
                table_name="admin_queue_preset_telemetry_events",
            )
        if "ix_admin_queue_preset_telemetry_events_preset_id" in index_names:
            op.drop_index(
                "ix_admin_queue_preset_telemetry_events_preset_id",
                table_name="admin_queue_preset_telemetry_events",
            )
        if "ix_admin_queue_preset_telemetry_events_queue_key" in index_names:
            op.drop_index(
                "ix_admin_queue_preset_telemetry_events_queue_key",
                table_name="admin_queue_preset_telemetry_events",
            )
        if "ix_admin_queue_preset_telemetry_events_queue_context" in index_names:
            op.drop_index(
                "ix_admin_queue_preset_telemetry_events_queue_context",
                table_name="admin_queue_preset_telemetry_events",
            )
        op.drop_table("admin_queue_preset_telemetry_events")
