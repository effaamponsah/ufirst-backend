"""identity: add country and beneficiary_relationship to users

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("country", sa.String(10), nullable=True),
        schema="identity",
    )
    op.add_column(
        "users",
        sa.Column("beneficiary_relationship", sa.String(50), nullable=True),
        schema="identity",
    )


def downgrade() -> None:
    op.drop_column("users", "beneficiary_relationship", schema="identity")
    op.drop_column("users", "country", schema="identity")
