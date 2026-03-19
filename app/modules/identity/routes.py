from __future__ import annotations

import hashlib
import hmac
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.modules.identity.models import KYCStatus, UserRole
from app.modules.identity.schemas import (
    KYCSubmissionResponse,
    SponsorBeneficiaryLinkResponse,
    SupabaseWebhookPayload,
    UserProfile,
)
from app.modules.identity.service import IdentityService

log = logging.getLogger(__name__)

router = APIRouter(tags=["identity"])


def _get_service(session: AsyncSession = Depends(get_db)) -> IdentityService:
    return IdentityService(session)


# ---------------------------------------------------------------------------
# Webhook — Supabase user.created
# ---------------------------------------------------------------------------


def _verify_supabase_signature(body: bytes, signature_header: str | None) -> None:
    """
    Supabase signs webhook payloads with HMAC-SHA256 using the webhook secret.
    Header format:  x-supabase-signature: sha256=<hex_digest>
    Skip verification in dev mode or when no secret is configured.
    """
    if settings.dev_mode or not settings.supabase_webhook_secret:
        return

    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing webhook signature.")

    expected = "sha256=" + hmac.new(
        settings.supabase_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")


@router.post("/auth/webhook/user-created", status_code=201)
async def user_created_webhook(
    request: Request,
    x_supabase_signature: str | None = Header(default=None),
    service: IdentityService = Depends(_get_service),
) -> UserProfile:
    body = await request.body()
    _verify_supabase_signature(body, x_supabase_signature)

    payload = SupabaseWebhookPayload.model_validate_json(body)

    if payload.type != "INSERT" or payload.table != "users":
        # Ignore updates and other event types
        raise HTTPException(status_code=200, detail="Event ignored.")

    record = payload.record
    raw_role = record.raw_app_meta_data.get("role", "")
    try:
        role = UserRole(raw_role)
    except ValueError:
        log.warning("Unknown role '%s' in Supabase webhook for user %s", raw_role, record.id)
        raise HTTPException(
            status_code=422, detail=f"Unknown role: '{raw_role}'. Cannot create user."
        )

    full_name = record.raw_user_meta_data.get("full_name")

    return await service.create_user_from_webhook(
        user_id=record.id,
        email=record.email or "",
        role=role,
        phone=record.phone,
        full_name=full_name,
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users/me")
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> UserProfile:
    return await service.get_user(current_user.id)


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
    "/users/me/beneficiaries/{beneficiary_id}",
    status_code=201,
    dependencies=[Depends(require_roles("sponsor"))],
)
async def link_beneficiary(
    beneficiary_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> SponsorBeneficiaryLinkResponse:
    return await service.link_beneficiary(current_user.id, beneficiary_id)


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
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    service: IdentityService = Depends(_get_service),
) -> KYCSubmissionResponse:
    # KYC documents are uploaded directly to object storage by the client.
    # The client sends back the storage path/reference in the request body.
    body = await request.json()
    document_refs: str | None = body.get("document_refs")
    return await service.submit_kyc(current_user.id, document_refs)


@router.post("/kyc/webhook", status_code=200)
async def kyc_provider_webhook(
    request: Request,
    service: IdentityService = Depends(_get_service),
) -> dict:  # type: ignore[type-arg]
    """
    Webhook from KYC provider (e.g. Onfido, Smile Identity).
    Signature verification and payload parsing are provider-specific —
    implement here when the provider is chosen.
    """
    body = await request.json()

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
