"""wallet: add card_payments table for Stripe card-funding fallback (Phase 3.8)

Revision ID: 20260319_0007
Revises: 20260319_0006
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260319_0007"
down_revision = "20260319_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "card_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("funding_transfer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(255), nullable=False),
        sa.Column("auth_link", sa.Text(), nullable=False),
        sa.Column("card_last4", sa.String(4), nullable=True),
        sa.Column("card_brand", sa.String(50), nullable=True),
        sa.Column("fee_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["funding_transfer_id"],
            ["wallet.funding_transfers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("funding_transfer_id", name="uq_card_payments_transfer"),
        sa.UniqueConstraint("stripe_payment_intent_id", name="uq_card_payments_intent_id"),
        schema="wallet",
    )
    op.create_index(
        "ix_card_payments_stripe_payment_intent_id",
        "card_payments",
        ["stripe_payment_intent_id"],
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_index("ix_card_payments_stripe_payment_intent_id", table_name="card_payments", schema="wallet")
    op.drop_table("card_payments", schema="wallet")
