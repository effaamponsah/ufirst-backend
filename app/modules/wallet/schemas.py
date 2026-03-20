from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field
from typing import Annotated

from app.modules.wallet.models import EntryType, PaymentMethod, PaymentState, WalletStatus


class WalletResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    owner_id: UUID
    currency: str
    available_balance: int
    reserved_balance: int
    status: WalletStatus
    created_at: datetime


class LedgerEntryResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    wallet_id: UUID
    entry_type: EntryType
    amount: int
    currency: str
    balance_after: int
    reference_type: str
    reference_id: UUID
    description: str | None
    created_at: datetime


class FundingTransferResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    wallet_id: UUID
    sponsor_id: UUID
    payment_method: PaymentMethod
    payment_state: PaymentState
    source_amount: int
    source_currency: str
    fx_rate: Decimal
    dest_amount: int
    dest_currency: str
    fee_amount: int
    idempotency_key: str
    external_payment_ref: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class InitiateFundingRequest(BaseModel):
    payment_method: PaymentMethod
    source_amount: Annotated[int, Field(gt=0, description="Amount in minor units (must be positive)")]
    source_currency: str
    dest_currency: str | None = None          # defaults to source_currency when omitted
    beneficiary_wallet_id: UUID | None = None  # fund a beneficiary's wallet
    bank_account_id: str | None = None         # pre-select a linked bank account (OB only)


class UpdateFundingStateRequest(BaseModel):
    payment_state: PaymentState
    external_payment_ref: str | None = None
    failure_reason: str | None = None


# ---------------------------------------------------------------------------
# Phase 3 — new response schemas
# ---------------------------------------------------------------------------


class FundingInitiateResponse(BaseModel):
    """Returned by POST /funding/initiate in Phase 3."""
    funding_transfer_id: UUID
    # auth_link is the bank redirect URL (OB) or Stripe client_secret (card).
    # None for payment methods that require no redirect (ACH/mobile-money stubs).
    auth_link: str | None
    payment_method: PaymentMethod
    payment_state: PaymentState
    source_amount: int
    source_currency: str
    dest_amount: int
    dest_currency: str


class FundingStatusResponse(BaseModel):
    """Returned by GET /funding/{id}/status — polled by the frontend."""
    model_config = {"from_attributes": True}

    id: UUID
    payment_state: PaymentState
    payment_state_changed_at: datetime
    external_payment_ref: str | None
    failure_reason: str | None


class BankConnectionResponse(BaseModel):
    """Safe view of a SponsorBankConnection — never exposes decrypted account numbers."""
    model_config = {"from_attributes": True}

    id: UUID
    sponsor_id: UUID
    aggregator: str
    account_holder_name: str
    provider_id: str
    provider_display_name: str
    currency: str
    status: str
    consent_expires_at: datetime | None
    created_at: datetime


class StartBankLinkResponse(BaseModel):
    auth_link: str
