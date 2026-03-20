"""identity: make users.email nullable for phone-only signups

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "email", nullable=True, schema="identity")


def downgrade() -> None:
    op.alter_column("users", "email", nullable=False, schema="identity")
