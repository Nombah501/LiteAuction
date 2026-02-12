"""add appeal sla and escalation fields

Revision ID: 0008_add_appeal_sla_fields
Revises: 0007_appeal_mod_actions
Create Date: 2026-02-13 06:30:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008_add_appeal_sla_fields"
down_revision: str | None = "0007_appeal_mod_actions"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("appeals", sa.Column("in_review_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appeals", sa.Column("sla_deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appeals", sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "appeals",
        sa.Column("escalation_level", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )

    op.create_index("ix_appeals_sla_deadline_at", "appeals", ["sla_deadline_at"], unique=False)
    op.create_index("ix_appeals_escalated_at", "appeals", ["escalated_at"], unique=False)
    op.create_index(
        "ix_appeals_escalation_scan",
        "appeals",
        ["status", "escalated_at", "sla_deadline_at"],
        unique=False,
    )

    op.execute(
        """
        UPDATE appeals
        SET
            in_review_started_at = CASE
                WHEN status = 'IN_REVIEW' THEN COALESCE(updated_at, created_at)
                ELSE NULL
            END,
            sla_deadline_at = CASE
                WHEN status = 'OPEN' THEN created_at + INTERVAL '24 hours'
                WHEN status = 'IN_REVIEW' THEN COALESCE(updated_at, created_at) + INTERVAL '12 hours'
                ELSE NULL
            END,
            escalation_level = 0,
            escalated_at = NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_appeals_escalation_scan", table_name="appeals")
    op.drop_index("ix_appeals_escalated_at", table_name="appeals")
    op.drop_index("ix_appeals_sla_deadline_at", table_name="appeals")

    op.drop_column("appeals", "escalation_level")
    op.drop_column("appeals", "escalated_at")
    op.drop_column("appeals", "sla_deadline_at")
    op.drop_column("appeals", "in_review_started_at")
