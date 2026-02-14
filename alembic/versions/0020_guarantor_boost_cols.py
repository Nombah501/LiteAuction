"""add guarantor priority boost columns

Revision ID: 0020_guarantor_boost_cols
Revises: 0019_points_guarantor_boost_evt
Create Date: 2026-02-14 18:16:00
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_guarantor_boost_cols"
down_revision: str | None = "0019_points_guarantor_boost_evt"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "guarantor_requests",
        sa.Column("priority_boost_points_spent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "guarantor_requests",
        sa.Column("priority_boosted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_guarantor_requests_priority_boosted_at",
        "guarantor_requests",
        ["priority_boosted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_guarantor_requests_priority_boosted_at", table_name="guarantor_requests")
    op.drop_column("guarantor_requests", "priority_boosted_at")
    op.drop_column("guarantor_requests", "priority_boost_points_spent")
