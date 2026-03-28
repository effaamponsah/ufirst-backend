"""
Transaction module ORM models.

Authorization status flow:
  AUTHORIZED → CLEARED (on clearing)
             → REVERSED (on reversal)
             → DECLINED (on decline — never stored as AUTHORIZED)

Dispute status flow:
  OPEN → INVESTIGATING → RESOLVED
"""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuthorizationStatus(str, enum.Enum):
    AUTHORIZED = "authorized"
    DECLINED = "declined"
    REVERSED = "reversed"
    CLEARED = "cleared"


class DisputeStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class Authorization(Base):
    __tablename__ = "authorizations"
    __table_args__ = {"schema": "transaction"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    card_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    merchant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_category_code: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    amount: Mapped[int] = mapped_column(Integer(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[AuthorizationStatus] = mapped_column(
        String(20), nullable=False
    )
    # Processor's reference — natural idempotency key for webhooks
    processor_auth_ref: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # Only set when DECLINED
    decline_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class Clearing(Base):
    __tablename__ = "clearings"
    __table_args__ = {"schema": "transaction"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    authorization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("transaction.authorizations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    cleared_amount: Mapped[int] = mapped_column(Integer(), nullable=False)
    cleared_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    processor_clearing_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    cleared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Dispute(Base):
    __tablename__ = "disputes"
    __table_args__ = {"schema": "transaction"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    authorization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("transaction.authorizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[DisputeStatus] = mapped_column(
        String(20), nullable=False, default=DisputeStatus.OPEN
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
