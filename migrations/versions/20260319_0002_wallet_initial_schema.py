"""wallet: initial schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wallets",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "available_balance",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "reserved_balance",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallets"),
        sa.UniqueConstraint("owner_id", name="uq_wallets_owner_id"),
        schema="wallet",
    )

    op.create_table(
        "ledger_entries",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("wallet_id", sa.UUID(), nullable=False),
        sa.Column("entry_type", sa.String(10), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(50), nullable=False),
        sa.Column("reference_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # NO updated_at — this table is append-only.
        sa.ForeignKeyConstraint(
            ["wallet_id"],
            ["wallet.wallets.id"],
            name="fk_ledger_entries_wallet_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ledger_entries"),
        schema="wallet",
    )

    op.create_table(
        "funding_transfers",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sponsor_id", sa.UUID(), nullable=False),
        sa.Column("wallet_id", sa.UUID(), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column(
            "payment_state",
            sa.String(30),
            nullable=False,
            server_default="initiated",
        ),
        sa.Column(
            "payment_state_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_amount", sa.Integer(), nullable=False),
        sa.Column("source_currency", sa.String(3), nullable=False),
        sa.Column(
            "fx_rate",
            sa.Numeric(20, 10),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("fx_rate_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dest_amount", sa.Integer(), nullable=False),
        sa.Column("dest_currency", sa.String(3), nullable=False),
        sa.Column(
            "fee_amount",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("external_payment_ref", sa.String(255), nullable=True),
        sa.Column("failure_reason", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"],
            ["wallet.wallets.id"],
            name="fk_funding_transfers_wallet_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_funding_transfers"),
        sa.UniqueConstraint("idempotency_key", name="uq_funding_transfers_idempotency_key"),
        schema="wallet",
    )

    # Indexes
    op.create_index(
        "ix_ledger_entries_wallet_id",
        "ledger_entries",
        ["wallet_id"],
        schema="wallet",
    )
    op.create_index(
        "ix_ledger_entries_reference",
        "ledger_entries",
        ["reference_type", "reference_id"],
        schema="wallet",
    )
    op.create_index(
        "ix_funding_transfers_wallet_id",
        "funding_transfers",
        ["wallet_id"],
        schema="wallet",
    )
    op.create_index(
        "ix_funding_transfers_sponsor_id",
        "funding_transfers",
        ["sponsor_id"],
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_table("funding_transfers", schema="wallet")
    op.drop_table("ledger_entries", schema="wallet")
    op.drop_table("wallets", schema="wallet")
