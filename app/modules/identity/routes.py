from __future__ import annotations

import hashlib
import hmac
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.storage import SupabaseStorageClient
from app.core.database import get_db
from app.modules.identity.models import KYCStatus
from app.modules.identity.schemas import (
    CompleteProfileRequest,
    CreateBeneficiaryRequest,
    KYCSubmissionResponse,
    UserProfile,
)
from app.modules.identity.service import IdentityService

log = logging.getLogger(__name__)

router = APIRouter(tags=["identity"])


def _get_service(session: AsyncSession = Depends(get_db)) -> IdentityService:
    return IdentityService(session)


# ---------------------------------------------------------------------------
# Webhook signature verification helpers
# ---------------------------------------------------------------------------


def _verify_hmac_sha256(
    body: bytes,
    signature_header: str | None,
    secret: str,
    *,
    header_prefix: str = "sha256=",
    error_label: str = "webhook",
) -> None:
    """
    Verify an HMAC-SHA256 webhook signature.
    Raises 401 if the signature is missing or invalid.
    """
    if not signature_header:
        raise HTTPException(
            status_code=401, detail=f"Missing {error_label} signature header."
        )

    expected = header_prefix + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(
            status_code=401, detail=f"Invalid {error_label} signature."
        )


def _verify_kyc_signature(body: bytes, signature_header: str | None) -> None:
    """
    Verify the KYC provider webhook signature (HMAC-SHA256).
    Header: x-kyc-signature: sha256=<hex_digest>

    Skipped in dev mode only. Fails closed when KYC_WEBHOOK_SECRET is unset
    in a non-dev environment.
    """
    if settings.dev_mode:
        return

    if not settings.kyc_webhook_secret:
        raise HTTPException(
            status_code=500,
            detail="KYC_WEBHOOK_SECRET is not configured. Cannot verify webhook.",
        )

    _verify_hmac_sha256(
        body, signature_header, settings.kyc_webhook_secret, error_label="KYC webhook"
    )


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


@router.post("/onboarding/complete-profile", status_code=200)
async def complete_profile(
    body: CompleteProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> UserProfile:
    """
    Upsert profile data (country, phone, full_name, beneficiary_relationship)
    onto the skeleton record created by the auth middleware on first login.
    Safe to call multiple times (idempotent for the same values).
    """
    return await service.complete_profile(current_user.id, body)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users/me")
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> UserProfile:
    return await service.get_or_create_user(
        current_user.id, email=current_user.email, role=current_user.role
    )


@router.get("/users/{user_id}", dependencies=[Depends(require_roles("admin", "ops_agent"))])
async def get_user(
    user_id: UUID,
    service: IdentityService = Depends(_get_service),
) -> UserProfile:
    return await service.get_user(user_id)


# ---------------------------------------------------------------------------
# Sponsor ↔ Beneficiary links
# ---------------------------------------------------------------------------


@router.get("/users/me/beneficiaries", dependencies=[Depends(require_roles("sponsor"))])
async def list_beneficiaries(
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> list[UserProfile]:
    return await service.list_beneficiaries(current_user.id)


@router.post(
    "/users/me/beneficiaries",
    status_code=201,
    dependencies=[Depends(require_roles("sponsor"))],
)
async def create_beneficiary(
    body: CreateBeneficiaryRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> UserProfile:
    return await service.create_beneficiary(current_user.id, body)


@router.delete(
    "/users/me/beneficiaries/{beneficiary_id}",
    status_code=204,
    dependencies=[Depends(require_roles("sponsor"))],
)
async def remove_beneficiary(
    beneficiary_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> None:
    await service.remove_beneficiary_link(current_user.id, beneficiary_id)


# ---------------------------------------------------------------------------
# KYC
# ---------------------------------------------------------------------------


@router.get("/kyc/status")
async def kyc_status(
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> UserProfile:
    return await service.get_user(current_user.id)


@router.post("/kyc/submit", status_code=201)
async def submit_kyc(
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
    document_front: UploadFile | None = File(default=None),
    document_back: UploadFile | None = File(default=None),
) -> KYCSubmissionResponse:
    storage = SupabaseStorageClient()
    bucket = settings.kyc_bucket

    slots = [("front", document_front), ("back", document_back)]
    refs: list[str] = []
    for slot, upload in slots:
        if upload is None or not upload.filename:
            continue
        path = f"{current_user.id}/{slot}/{upload.filename}"
        contents = await upload.read()
        url = await storage.upload(bucket, path, contents, upload.filename)
        refs.append(url)

    document_refs: str | None = json.dumps(refs) if refs else None
    return await service.submit_kyc(current_user.id, document_refs)


@router.post("/kyc/webhook", status_code=200)
async def kyc_provider_webhook(
    request: Request,
    x_kyc_signature: str | None = Header(default=None),
    service: IdentityService = Depends(_get_service),
) -> dict:  # type: ignore[type-arg]
    """
    Webhook from KYC provider (e.g. Onfido, Smile Identity).
    Signature verification uses HMAC-SHA256 over the raw request body.
    Header: x-kyc-signature: sha256=<hex_digest>
    """
    raw_body = await request.body()
    _verify_kyc_signature(raw_body, x_kyc_signature)

    body = json.loads(raw_body)

    user_id: UUID | None = body.get("user_id")
    status_str: str | None = body.get("status")

    if not user_id or not status_str:
        raise HTTPException(status_code=422, detail="Missing user_id or status.")

    try:
        new_status = KYCStatus(status_str)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Unknown KYC status: {status_str}")

    await service.update_kyc_status(
        UUID(str(user_id)),
        new_status,
        provider_ref=body.get("provider_ref"),
    )
    return {"received": True}
