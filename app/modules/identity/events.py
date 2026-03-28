from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.modules.identity.models import KYCStatus, UserRole


class UserCreated(BaseModel):
    user_id: UUID
    role: UserRole | None
    email: str | None
    country: str | None = None


class KYCStatusChanged(BaseModel):
    user_id: UUID
    old_status: KYCStatus
    new_status: KYCStatus


class SponsorBeneficiaryLinked(BaseModel):
    sponsor_id: UUID
    beneficiary_id: UUID
