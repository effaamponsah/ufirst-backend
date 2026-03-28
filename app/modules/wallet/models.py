from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
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
        Enum(WalletStatus, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]),
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
        Enum(EntryType, native_enum=False, length=10, values_callable=lambda x: [e.value for e in x]), nullable=False
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
        Enum(PaymentMethod, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]), nullable=False
    )
    payment_state: Mapped[PaymentState] = mapped_column(
        Enum(PaymentState, native_enum=False, length=30, values_callable=lambda x: [e.value for e in x]),
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


# ---------------------------------------------------------------------------
# Phase 3 — Open Banking models
# ---------------------------------------------------------------------------


class BankConnectionStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class SponsorBankConnection(Base):
    """
    A linked bank account for a sponsor, used for open banking payments.

    account_identifier_encrypted stores the IBAN / sort-code+account
    encrypted with AES-256-GCM (see app.core.encryption).
    """

    __tablename__ = "sponsor_bank_connections"
    __table_args__ = {"schema": "wallet"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    sponsor_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aggregator: Mapped[str] = mapped_column(String(50), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_identifier_encrypted: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    account_holder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    consent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[BankConnectionStatus] = mapped_column(
        Enum(BankConnectionStatus, native_enum=False, length=20, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=BankConnectionStatus.ACTIVE,
        server_default=BankConnectionStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OpenBankingPayment(Base):
    """
    One-to-one record linking a FundingTransfer to its aggregator payment.
    Created after a successful aggregator.initiate() call.
    """

    __tablename__ = "open_banking_payments"
    __table_args__ = {"schema": "wallet"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    funding_transfer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wallet.funding_transfers.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    aggregator: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregator_payment_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    auth_link: Mapped[str] = mapped_column(Text, nullable=False)
    bank_status: Mapped[str | None] = mapped_column(String(100))
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    webhook_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OpenBankingWebhookLog(Base):
    """
    Raw webhook audit log — persisted before any processing.
    Every inbound webhook is logged here regardless of validity.
    """

    __tablename__ = "open_banking_webhooks_log"
    __table_args__ = {"schema": "wallet"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    aggregator: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # type: ignore[type-arg]
    signature_valid: Mapped[bool] = mapped_column(nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Phase 3.8 — Card payment (Stripe fallback)
# ---------------------------------------------------------------------------


class CardPayment(Base):
    """
    Stripe PaymentIntent record for card-funded transfers.
    card_last4 / card_brand are populated after Stripe confirms the payment.
    """

    __tablename__ = "card_payments"
    __table_args__ = {"schema": "wallet"}

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    funding_transfer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("wallet.funding_transfers.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    stripe_payment_intent_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # Stripe client_secret — frontend passes this to Stripe.js to complete payment
    auth_link: Mapped[str] = mapped_column(Text, nullable=False)
    card_last4: Mapped[str | None] = mapped_column(String(4))
    card_brand: Mapped[str | None] = mapped_column(String(50))
    fee_amount: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
