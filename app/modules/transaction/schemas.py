from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.transaction.models import AuthorizationStatus, DisputeStatus


class AuthorizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    card_id: UUID
    wallet_id: UUID
    merchant_name: str
    merchant_category_code: str | None
    amount: int
    currency: str
    status: AuthorizationStatus
    processor_auth_ref: str
    decline_reason: str | None
    authorized_at: datetime
    created_at: datetime
    updated_at: datetime | None


class ClearingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    authorization_id: UUID
    cleared_amount: int
    cleared_currency: str
    processor_clearing_ref: str | None
    cleared_at: datetime
    created_at: datetime


class DisputeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    authorization_id: UUID
    reason: str
    status: DisputeStatus
    opened_at: datetime
    resolved_at: datetime | None
    resolution: str | None
    created_at: datetime
    updated_at: datetime | None


# ---------------------------------------------------------------------------
# Webhook payloads (inbound from card processor)
# ---------------------------------------------------------------------------


class AuthorizationWebhookPayload(BaseModel):
    """Incoming card processor authorization request."""

    processor_auth_ref: str
    card_token: str  # processor token — we look up the card by this
    merchant_name: str
    merchant_category_code: str | None = None
    amount: int
    currency: str


class AuthorizationDecisionResponse(BaseModel):
    """Synchronous response to the authorization webhook."""

    decision: str  # "APPROVED" or "DECLINED"
    reason: str | None = None
    authorization_id: UUID | None = None


class ClearingWebhookPayload(BaseModel):
    processor_auth_ref: str
    processor_clearing_ref: str | None = None
    cleared_amount: int
    cleared_currency: str


class ReversalWebhookPayload(BaseModel):
    processor_auth_ref: str


class OpenDisputeRequest(BaseModel):
    reason: str
