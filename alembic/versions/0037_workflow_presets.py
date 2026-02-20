"""add workflow preset tables

Revision ID: 0037_workflow_presets
Revises: 0036_admin_list_preferences
Create Date: 2026-02-20 09:15:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037_workflow_presets"
down_revision: str | None = "0036_admin_list_preferences"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("admin_queue_presets"):
        op.create_table(
            "admin_queue_presets",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("owner_subject_key", sa.String(length=128), nullable=False),
            sa.Column("queue_context", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=40), nullable=False),
            sa.Column("density", sa.String(length=16), nullable=False, server_default=sa.text("'standard'")),
            sa.Column("columns_json", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("filters_json", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("sort_json", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
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
                "queue_context IN ('moderation', 'appeals', 'risk', 'feedback')",
                name="ck_admin_queue_presets_admin_queue_presets_queue_context_values",
            ),
            sa.CheckConstraint(
                "char_length(name) >= 1 AND char_length(name) <= 40",
                name="ck_admin_queue_presets_admin_queue_presets_name_length",
            ),
            sa.CheckConstraint(
                "density IN ('compact', 'standard', 'comfortable')",
                name="ck_admin_queue_presets_admin_queue_presets_density_values",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_admin_queue_presets"),
            sa.UniqueConstraint(
                "owner_subject_key",
                "queue_context",
                "name",
                name="uq_admin_queue_presets_owner_context_name",
            ),
        )

    preset_indexes = {index["name"] for index in inspector.get_indexes("admin_queue_presets")}
    if "ix_admin_queue_presets_owner_subject_key" not in preset_indexes:
        op.create_index(
            "ix_admin_queue_presets_owner_subject_key",
            "admin_queue_presets",
            ["owner_subject_key"],
            unique=False,
        )
    if "ix_admin_queue_presets_queue_context" not in preset_indexes:
        op.create_index(
            "ix_admin_queue_presets_queue_context",
            "admin_queue_presets",
            ["queue_context"],
            unique=False,
        )
    if "ix_admin_queue_presets_updated_at" not in preset_indexes:
        op.create_index(
            "ix_admin_queue_presets_updated_at",
            "admin_queue_presets",
            ["updated_at"],
            unique=False,
        )

    if not inspector.has_table("admin_queue_preset_selections"):
        op.create_table(
            "admin_queue_preset_selections",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("subject_key", sa.String(length=128), nullable=False),
            sa.Column("queue_context", sa.String(length=32), nullable=False),
            sa.Column("preset_id", sa.BigInteger(), nullable=True),
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
                "queue_context IN ('moderation', 'appeals', 'risk', 'feedback')",
                name="ck_admin_queue_preset_selections_admin_queue_preset_selections_queue_context_values",
            ),
            sa.ForeignKeyConstraint(
                ["preset_id"],
                ["admin_queue_presets.id"],
                name="fk_admin_queue_preset_selections_preset_id_admin_queue_presets",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_admin_queue_preset_selections"),
            sa.UniqueConstraint(
                "subject_key",
                "queue_context",
                name="uq_admin_queue_preset_selections_subject_context",
            ),
        )

    selection_indexes = {index["name"] for index in inspector.get_indexes("admin_queue_preset_selections")}
    if "ix_admin_queue_preset_selections_subject_key" not in selection_indexes:
        op.create_index(
            "ix_admin_queue_preset_selections_subject_key",
            "admin_queue_preset_selections",
            ["subject_key"],
            unique=False,
        )
    if "ix_admin_queue_preset_selections_queue_context" not in selection_indexes:
        op.create_index(
            "ix_admin_queue_preset_selections_queue_context",
            "admin_queue_preset_selections",
            ["queue_context"],
            unique=False,
        )

    if not inspector.has_table("admin_queue_preset_defaults"):
        op.create_table(
            "admin_queue_preset_defaults",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("queue_context", sa.String(length=32), nullable=False),
            sa.Column("preset_id", sa.BigInteger(), nullable=True),
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
                "queue_context IN ('moderation', 'appeals', 'risk', 'feedback')",
                name="ck_admin_queue_preset_defaults_admin_queue_preset_defaults_queue_context_values",
            ),
            sa.ForeignKeyConstraint(
                ["preset_id"],
                ["admin_queue_presets.id"],
                name="fk_admin_queue_preset_defaults_preset_id_admin_queue_presets",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name="pk_admin_queue_preset_defaults"),
            sa.UniqueConstraint("queue_context", name="uq_admin_queue_preset_defaults_queue_context"),
        )

    default_indexes = {index["name"] for index in inspector.get_indexes("admin_queue_preset_defaults")}
    if "ix_admin_queue_preset_defaults_queue_context" not in default_indexes:
        op.create_index(
            "ix_admin_queue_preset_defaults_queue_context",
            "admin_queue_preset_defaults",
            ["queue_context"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("admin_queue_preset_defaults"):
        default_indexes = {index["name"] for index in inspector.get_indexes("admin_queue_preset_defaults")}
        if "ix_admin_queue_preset_defaults_queue_context" in default_indexes:
            op.drop_index("ix_admin_queue_preset_defaults_queue_context", table_name="admin_queue_preset_defaults")
        op.drop_table("admin_queue_preset_defaults")

    if inspector.has_table("admin_queue_preset_selections"):
        selection_indexes = {index["name"] for index in inspector.get_indexes("admin_queue_preset_selections")}
        if "ix_admin_queue_preset_selections_queue_context" in selection_indexes:
            op.drop_index(
                "ix_admin_queue_preset_selections_queue_context",
                table_name="admin_queue_preset_selections",
            )
        if "ix_admin_queue_preset_selections_subject_key" in selection_indexes:
            op.drop_index(
                "ix_admin_queue_preset_selections_subject_key",
                table_name="admin_queue_preset_selections",
            )
        op.drop_table("admin_queue_preset_selections")

    if inspector.has_table("admin_queue_presets"):
        preset_indexes = {index["name"] for index in inspector.get_indexes("admin_queue_presets")}
        if "ix_admin_queue_presets_updated_at" in preset_indexes:
            op.drop_index("ix_admin_queue_presets_updated_at", table_name="admin_queue_presets")
        if "ix_admin_queue_presets_queue_context" in preset_indexes:
            op.drop_index("ix_admin_queue_presets_queue_context", table_name="admin_queue_presets")
        if "ix_admin_queue_presets_owner_subject_key" in preset_indexes:
            op.drop_index("ix_admin_queue_presets_owner_subject_key", table_name="admin_queue_presets")
        op.drop_table("admin_queue_presets")
