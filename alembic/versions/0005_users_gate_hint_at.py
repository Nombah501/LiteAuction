"""add users soft_gate_hint_sent_at

Revision ID: 0005_users_gate_hint_at
Revises: 0004_users_private_started_at
Create Date: 2026-02-12 13:15:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005_users_gate_hint_at"
down_revision: str | None = "0004_users_private_started_at"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("soft_gate_hint_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "soft_gate_hint_sent_at")
