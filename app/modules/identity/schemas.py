from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.modules.identity.models import KYCStatus, KYCSubmissionStatus, LinkStatus, UserRole


class UserProfile(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    email: str
    phone: str | None
    full_name: str | None
    role: UserRole
    kyc_status: KYCStatus
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


# ---------------------------------------------------------------------------
# Supabase webhook payload (user.created)
# ---------------------------------------------------------------------------


class SupabaseUserRecord(BaseModel):
    id: UUID
    email: str | None = None
    phone: str | None = None
    raw_app_meta_data: dict = {}
    raw_user_meta_data: dict = {}


class SupabaseWebhookPayload(BaseModel):
    model_config = {"populate_by_name": True}

    type: str        # INSERT | UPDATE | DELETE
    table: str       # users
    db_schema: str = ""   # "auth" — renamed to avoid clash with Pydantic's BaseModel.schema
    record: SupabaseUserRecord
