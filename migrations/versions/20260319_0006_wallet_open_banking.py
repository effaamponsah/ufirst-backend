"""wallet: add open banking tables (sponsor_bank_connections, open_banking_payments, open_banking_webhooks_log)

Revision ID: 20260319_0006
Revises: 20260319_0005
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260319_0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sponsor_bank_connections
    op.create_table(
        "sponsor_bank_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sponsor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregator", sa.String(50), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("account_identifier_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("account_holder_name", sa.String(255), nullable=False),
        sa.Column("provider_id", sa.String(100), nullable=False),
        sa.Column("provider_display_name", sa.String(255), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("consent_id", sa.String(255), nullable=False),
        sa.Column("consent_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="wallet",
    )
    op.create_index(
        "ix_sponsor_bank_connections_sponsor_id",
        "sponsor_bank_connections",
        ["sponsor_id"],
        schema="wallet",
    )

    # open_banking_payments
    op.create_table(
        "open_banking_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("funding_transfer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregator", sa.String(50), nullable=False),
        sa.Column("aggregator_payment_id", sa.String(255), nullable=False),
        sa.Column("auth_link", sa.Text(), nullable=False),
        sa.Column("bank_status", sa.String(100), nullable=True),
        sa.Column("failure_reason", sa.String(500), nullable=True),
        sa.Column("webhook_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["funding_transfer_id"],
            ["wallet.funding_transfers.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("funding_transfer_id", name="uq_ob_payments_transfer"),
        sa.UniqueConstraint("aggregator_payment_id", name="uq_ob_payments_aggregator_id"),
        schema="wallet",
    )
    op.create_index(
        "ix_open_banking_payments_aggregator_payment_id",
        "open_banking_payments",
        ["aggregator_payment_id"],
        schema="wallet",
    )

    # open_banking_webhooks_log
    op.create_table(
        "open_banking_webhooks_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("aggregator", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="wallet",
    )


def downgrade() -> None:
    op.drop_table("open_banking_webhooks_log", schema="wallet")
    op.drop_table("open_banking_payments", schema="wallet")
    op.drop_index("ix_sponsor_bank_connections_sponsor_id", table_name="sponsor_bank_connections", schema="wallet")
    op.drop_table("sponsor_bank_connections", schema="wallet")
