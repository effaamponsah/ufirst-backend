from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class TransactionAuthorized(BaseModel):
    authorization_id: UUID
    card_id: UUID
    wallet_id: UUID
    amount: int
    currency: str
    merchant_name: str


class TransactionDeclined(BaseModel):
    card_id: UUID
    wallet_id: UUID
    amount: int
    currency: str
    merchant_name: str
    reason: str


class TransactionCleared(BaseModel):
    authorization_id: UUID
    card_id: UUID
    wallet_id: UUID
    cleared_amount: int
    currency: str


class TransactionReversed(BaseModel):
    authorization_id: UUID
    card_id: UUID
    wallet_id: UUID
    amount: int
    currency: str
