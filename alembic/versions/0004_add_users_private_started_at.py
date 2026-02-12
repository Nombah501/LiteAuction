"""add users private_started_at

Revision ID: 0004_users_private_started_at
Revises: 0003_add_fraud_signals_table
Create Date: 2026-02-12 12:30:00
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_users_private_started_at"
down_revision: str | None = "0003_add_fraud_signals_table"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("private_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "private_started_at")
