from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class CardIssued(BaseModel):
    """Card ordered — physical card will be mailed by UP Nigeria."""
    card_id: UUID
    wallet_id: UUID
    owner_id: UUID  # beneficiary


class CardActivated(BaseModel):
    """Physical card confirmed dispatched and activated for POS use."""
    card_id: UUID
    owner_id: UUID


class CardFrozen(BaseModel):
    card_id: UUID
    owner_id: UUID
    reason: str | None


class CardUnfrozen(BaseModel):
    card_id: UUID
    owner_id: UUID


class CardCancelled(BaseModel):
    card_id: UUID
    owner_id: UUID
    reason: str | None


class CardSpendingControlsUpdated(BaseModel):
    card_id: UUID
    owner_id: UUID
