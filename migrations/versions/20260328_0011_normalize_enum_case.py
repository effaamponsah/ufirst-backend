"""normalize enum column values to lowercase

Revision ID: 0011
Revises: 0010
Create Date: 2026-03-28

SQLAlchemy 2.0.48 with str+enum.Enum and native_enum=False stores enum member
*names* (e.g. "SPONSOR") instead of *values* (e.g. "sponsor") by default.
After adding values_callable to all Enum columns the ORM now reads/writes
lowercase values correctly, but existing rows need a one-time normalisation.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS: list[tuple[str, str, list[str]]] = [
    ("identity", "users",                    ["role", "kyc_status"]),
    ("identity", "kyc_submissions",          ["status"]),
    ("identity", "sponsor_beneficiary_links",["status"]),
    ("wallet",   "wallets",                  ["status"]),
    ("wallet",   "ledger_entries",           ["entry_type"]),
    ("wallet",   "funding_transfers",        ["payment_state", "payment_method"]),
    ("wallet",   "sponsor_bank_connections", ["status"]),
]


def upgrade() -> None:
    for schema, table, columns in _COLUMNS:
        for col in columns:
            op.execute(
                f"UPDATE {schema}.{table}"
                f" SET {col} = LOWER({col})"
                f" WHERE {col} IS NOT NULL AND {col} != LOWER({col})"
            )


def downgrade() -> None:
    # Lowercase values are correct — no meaningful rollback.
    pass
