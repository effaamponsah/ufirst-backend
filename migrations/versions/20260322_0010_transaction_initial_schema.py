"""transaction: initial schema — authorizations, clearings, disputes

Revision ID: 0010
Revises: 0009
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the transaction schema
    op.execute("CREATE SCHEMA IF NOT EXISTS transaction")

    # transaction.authorizations
    op.create_table(
        "authorizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_name", sa.String(255), nullable=False),
        sa.Column("merchant_category_code", sa.String(10), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("processor_auth_ref", sa.String(255), nullable=False),
        sa.Column("decline_reason", sa.String(100), nullable=True),
        sa.Column(
            "authorized_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("processor_auth_ref", name="uq_authorizations_processor_auth_ref"),
        schema="transaction",
    )
    op.create_index(
        "ix_transaction_authorizations_card_id",
        "authorizations",
        ["card_id"],
        schema="transaction",
    )
    op.create_index(
        "ix_transaction_authorizations_wallet_id",
        "authorizations",
        ["wallet_id"],
        schema="transaction",
    )
    op.create_index(
        "ix_transaction_authorizations_processor_auth_ref",
        "authorizations",
        ["processor_auth_ref"],
        schema="transaction",
    )

    # transaction.clearings
    op.create_table(
        "clearings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "authorization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "transaction.authorizations.id",
                ondelete="RESTRICT",
                name="fk_clearings_authorization_id",
            ),
            nullable=False,
            unique=True,
        ),
        sa.Column("cleared_amount", sa.Integer(), nullable=False),
        sa.Column("cleared_currency", sa.String(3), nullable=False),
        sa.Column("processor_clearing_ref", sa.String(255), nullable=True),
        sa.Column(
            "cleared_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="transaction",
    )

    # transaction.disputes
    op.create_table(
        "disputes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "authorization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "transaction.authorizations.id",
                ondelete="RESTRICT",
                name="fk_disputes_authorization_id",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        schema="transaction",
    )


def downgrade() -> None:
    op.drop_table("disputes", schema="transaction")
    op.drop_table("clearings", schema="transaction")
    op.drop_index(
        "ix_transaction_authorizations_processor_auth_ref",
        table_name="authorizations",
        schema="transaction",
    )
    op.drop_index(
        "ix_transaction_authorizations_wallet_id",
        table_name="authorizations",
        schema="transaction",
    )
    op.drop_index(
        "ix_transaction_authorizations_card_id",
        table_name="authorizations",
        schema="transaction",
    )
    op.drop_table("authorizations", schema="transaction")
    op.execute("DROP SCHEMA IF EXISTS transaction CASCADE")
