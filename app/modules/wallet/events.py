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
