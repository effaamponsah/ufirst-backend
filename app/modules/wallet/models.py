from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WalletStatus(str, enum.Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class EntryType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class PaymentMethod(str, enum.Enum):
    OPEN_BANKING = "open_banking"
    CARD = "card"
    ACH = "ach"
    MOBILE_MONEY = "mobile_money"


class PaymentState(str, enum.Enum):
    INITIATED = "initiated"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    AUTHORIZING = "authorizing"
    AWAITING_SETTLEMENT = "awaiting_settlement"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = {"schema": "wallet"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    # No FK constraint across schemas — enforced at the application layer
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # All balances in minor units (e.g. pence, kobo). NEVER float.
    available_balance: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default="0"
    )
    reserved_balance: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default="0"
    )
    status: Mapped[WalletStatus] = mapped_column(
        Enum(WalletStatus, native_enum=False, length=20),
        nullable=False,
        default=WalletStatus.ACTIVE,
        server_default=WalletStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    ledger_entries: Mapped[list[LedgerEntry]] = relationship(back_populates="wallet")
    funding_transfers: Mapped[list[FundingTransfer]] = relationship(back_populates="wallet")


class LedgerEntry(Base):
    """
    Append-only financial ledger.

    CRITICAL: NO UPDATE, NO DELETE — ever. Corrections use reversal entries.
    This table intentionally has no `updated_at` column.
    """

    __tablename__ = "ledger_entries"
    __table_args__ = {"schema": "wallet"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wallet.wallets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_type: Mapped[EntryType] = mapped_column(
        Enum(EntryType, native_enum=False, length=10), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer(), nullable=False)   # always positive
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer(), nullable=False)  # snapshot for audit
    reference_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # NO updated_at — this table is append-only.

    wallet: Mapped[Wallet] = relationship(back_populates="ledger_entries")


class FundingTransfer(Base):
    __tablename__ = "funding_transfers"
    __table_args__ = (
        # Idempotency is scoped per-sponsor — two different sponsors may use the same key
        UniqueConstraint("sponsor_id", "idempotency_key", name="uq_funding_transfers_sponsor_idempotency"),
        {"schema": "wallet"},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    sponsor_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    wallet_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wallet.wallets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, native_enum=False, length=20), nullable=False
    )
    payment_state: Mapped[PaymentState] = mapped_column(
        Enum(PaymentState, native_enum=False, length=30),
        nullable=False,
        default=PaymentState.INITIATED,
        server_default=PaymentState.INITIATED.value,
    )
    payment_state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_amount: Mapped[int] = mapped_column(Integer(), nullable=False)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # fx_rate stored as NUMERIC to avoid float precision loss
    fx_rate: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("1.0")
    )
    fx_rate_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dest_amount: Mapped[int] = mapped_column(Integer(), nullable=False)
    dest_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fee_amount: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default="0"
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    external_payment_ref: Mapped[str | None] = mapped_column(String(255))
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    wallet: Mapped[Wallet] = relationship(back_populates="funding_transfers")
