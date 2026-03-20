from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class WalletCreated(BaseModel):
    wallet_id: UUID
    owner_id: UUID
    currency: str


class WalletFunded(BaseModel):
    wallet_id: UUID
    amount: int
    currency: str
    funding_transfer_id: UUID


class WalletDebited(BaseModel):
    wallet_id: UUID
    amount: int
    currency: str
    reference_type: str
    reference_id: UUID


# ---------------------------------------------------------------------------
# Phase 3 — Open Banking / Funding events
# ---------------------------------------------------------------------------


class FundingInitiated(BaseModel):
    funding_transfer_id: UUID
    sponsor_id: UUID
    payment_method: str
    amount: int
    currency: str


class FundingPaymentReceived(BaseModel):
    """Aggregator confirmed the payment was executed / funds are in flight."""
    funding_transfer_id: UUID
    aggregator_payment_id: str


class FundingFailed(BaseModel):
    funding_transfer_id: UUID
    reason: str | None


class FundingAuthorizationExpired(BaseModel):
    """Sponsor did not complete bank auth within the 15-minute window."""
    funding_transfer_id: UUID


# ---------------------------------------------------------------------------
# Phase 3 — Bank connection events
# ---------------------------------------------------------------------------


class BankConnectionCreated(BaseModel):
    connection_id: UUID
    sponsor_id: UUID
    provider_display_name: str


class BankConsentExpiring(BaseModel):
    """Consent expires within the warning window (default 7 days)."""
    connection_id: UUID
    sponsor_id: UUID
    provider_display_name: str
    consent_expires_at: str  # ISO 8601


class BankConnectionRevoked(BaseModel):
    connection_id: UUID
    sponsor_id: UUID
