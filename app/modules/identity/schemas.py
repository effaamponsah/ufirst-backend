from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.modules.identity.models import KYCStatus, KYCSubmissionStatus, LinkStatus, UserRole


class UserProfile(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    email: str | None
    phone: str | None
    full_name: str | None
    role: UserRole
    kyc_status: KYCStatus
    country: str | None
    beneficiary_relationship: str | None
    created_at: datetime


class SponsorBeneficiaryLinkResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    sponsor_id: UUID
    beneficiary_id: UUID
    status: LinkStatus
    created_at: datetime


class KYCSubmissionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    user_id: UUID
    status: KYCSubmissionStatus
    provider_ref: str | None
    created_at: datetime


class CompleteProfileRequest(BaseModel):
    """Payload for POST /onboarding/complete-profile."""

    email: str | None = None            # Required for phone-OTP signups with no email in JWT
    country: str | None = None          # ISO 3166-1 alpha-2, e.g. "GB"
    phone: str | None = None
    full_name: str | None = None
    beneficiary_relationship: str | None = None  # e.g. "spouse", "parent", "sibling"


class CreateBeneficiaryRequest(BaseModel):
    """Payload for POST /users/me/beneficiaries — sponsor creates a beneficiary."""

    full_name: str
    phone: str
    country: str                         # ISO 3166-1 alpha-2, e.g. "NG"
    beneficiary_relationship: str        # e.g. "spouse", "parent", "sibling"
    email: str | None = None
