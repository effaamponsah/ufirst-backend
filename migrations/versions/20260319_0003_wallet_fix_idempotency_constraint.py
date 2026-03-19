"""wallet: scope idempotency key constraint to sponsor

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-19

Replace the global unique constraint on funding_transfers.idempotency_key with a
composite (sponsor_id, idempotency_key) constraint so two different sponsors can
legitimately reuse the same key without collision.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_funding_transfers_idempotency_key",
        "funding_transfers",
        schema="wallet",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_funding_transfers_sponsor_idempotency",
        "funding_transfers",
        ["sponsor_id", "idempotency_key"],
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_funding_transfers_sponsor_idempotency",
        "funding_transfers",
        schema="wallet",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_funding_transfers_idempotency_key",
        "funding_transfers",
        ["idempotency_key"],
        schema="wallet",
    )
