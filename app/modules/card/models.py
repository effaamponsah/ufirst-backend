from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CardStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    FROZEN = "frozen"
    CANCELLED = "cancelled"


class CardEventType(str, enum.Enum):
    ISSUED = "issued"
    ACTIVATED = "activated"
    FROZEN = "frozen"
    UNFROZEN = "unfrozen"
    CANCELLED = "cancelled"
    CONTROLS_UPDATED = "controls_updated"


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = {"schema": "card"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    wallet_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    owner_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    # Never store raw PANs — only processor-issued tokens
    processor_token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    card_program_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[CardStatus] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    # JSONB: { "daily_limit": int, "categories": [...], "merchant_allowlist": [...] }
    spending_controls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CardEvent(Base):
    """Append-only audit log of every card status change."""

    __tablename__ = "card_events"
    __table_args__ = {"schema": "card"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    card_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("card.cards.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[CardEventType] = mapped_column(
        String(50), nullable=False
    )
    actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Snapshot of spending_controls at the time of the update (for CONTROLS_UPDATED events)
    event_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # No updated_at — append-only
