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
    beneficiary_wallet_id: UUID | None = None  # for sponsor-funded transfers


class UpdateFundingStateRequest(BaseModel):
    payment_state: PaymentState
    external_payment_ref: str | None = None
    failure_reason: str | None = None
