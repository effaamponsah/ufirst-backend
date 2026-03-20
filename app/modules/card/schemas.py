from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, field_validator

from app.modules.card.models import CardEventType, CardStatus


class SpendingControls(BaseModel):
    """Spending control rules attached to a card."""

    daily_limit: int | None = None          # minor units; None = no limit
    categories: list[str] | None = None     # MCC category allowlist
    merchant_allowlist: list[str] | None = None

    @field_validator("daily_limit")
    @classmethod
    def _positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("daily_limit must be a positive integer")
        return v


class IssueCardRequest(BaseModel):
    """Body for POST /cards/ — sponsor issues a card for a beneficiary."""

    beneficiary_id: UUID
    spending_controls: SpendingControls | None = None


class UpdateSpendingControlsRequest(BaseModel):
    spending_controls: SpendingControls


class CardResponse(BaseModel):
    """Safe card view — never includes the raw PAN."""

    model_config = {"from_attributes": True}

    id: UUID
    wallet_id: UUID
    owner_id: UUID
    # processor_token intentionally excluded from the API response
    card_program_id: str
    status: CardStatus
    spending_controls: dict | None = None  # type: ignore[type-arg]
    issued_at: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CardEventResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    card_id: UUID
    event_type: CardEventType
    actor_id: UUID | None
    reason: str | None
    event_metadata: dict | None = None  # type: ignore[type-arg]
    created_at: datetime
