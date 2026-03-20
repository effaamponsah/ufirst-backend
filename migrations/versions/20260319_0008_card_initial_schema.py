"""card: add cards and card_events tables (Phase 4)

Revision ID: 20260319_0008
Revises: 20260319_0007
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260319_0008"
down_revision = "20260319_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS card")

    # Create enums in the card schema
    op.execute("CREATE TYPE card.cardstatus AS ENUM ('pending', 'active', 'frozen', 'cancelled')")
    op.execute(
        "CREATE TYPE card.cardeventtype AS ENUM "
        "('issued', 'activated', 'frozen', 'unfrozen', 'cancelled', 'controls_updated')"
    )

    op.create_table(
        "cards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processor_token", sa.String(255), nullable=False),
        sa.Column("card_program_id", sa.String(100), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("spending_controls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("processor_token", name="uq_cards_processor_token"),
        schema="card",
    )
    op.create_index("ix_cards_wallet_id", "cards", ["wallet_id"], schema="card")
    op.create_index("ix_cards_owner_id", "cards", ["owner_id"], schema="card")

    op.create_table(
        "card_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("event_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["card.cards.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="card",
    )
    op.create_index("ix_card_events_card_id", "card_events", ["card_id"], schema="card")


def downgrade() -> None:
    op.drop_index("ix_card_events_card_id", table_name="card_events", schema="card")
    op.drop_table("card_events", schema="card")
    op.drop_index("ix_cards_owner_id", table_name="cards", schema="card")
    op.drop_index("ix_cards_wallet_id", table_name="cards", schema="card")
    op.drop_table("cards", schema="card")
    op.execute("DROP TYPE IF EXISTS card.cardeventtype")
    op.execute("DROP TYPE IF EXISTS card.cardstatus")
    op.execute("DROP SCHEMA IF EXISTS card")
