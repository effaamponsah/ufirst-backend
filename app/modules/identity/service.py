"""
IdentityService — public interface for the identity module.

All other modules MUST use this service to read user or link data.
Never import from identity.repository or identity.models directly
from outside this module.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.exceptions import NotFound, PermissionDenied
from app.modules.identity import repository as repo
from app.modules.identity.events import KYCStatusChanged, SponsorBeneficiaryLinked, UserCreated
from app.modules.identity.models import KYCStatus, LinkStatus, UserRole
from app.modules.identity.schemas import (
    KYCSubmissionResponse,
    SponsorBeneficiaryLinkResponse,
    UserProfile,
)


class IdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    async def get_user(self, user_id: UUID) -> UserProfile:
        user = await repo.get_user(self._session, user_id)
        if user is None:
            raise NotFound(f"User {user_id} not found.")
        return UserProfile.model_validate(user)

    async def create_user_from_webhook(
        self,
        *,
        user_id: UUID,
        email: str,
        role: UserRole,
        phone: str | None = None,
        full_name: str | None = None,
    ) -> UserProfile:
        # Idempotent — Supabase may deliver the webhook more than once
        existing = await repo.get_user(self._session, user_id)
        if existing is not None:
            return UserProfile.model_validate(existing)

        user = await repo.create_user(
            self._session,
            user_id=user_id,
            email=email,
            role=role,
            phone=phone,
            full_name=full_name,
        )
        await events.publish(UserCreated(user_id=user.id, role=user.role, email=user.email))
        return UserProfile.model_validate(user)

    # ------------------------------------------------------------------
    # KYC
    # ------------------------------------------------------------------

    async def submit_kyc(self, user_id: UUID, document_refs: str | None) -> KYCSubmissionResponse:
        user = await repo.get_user(self._session, user_id)
        if user is None:
            raise NotFound(f"User {user_id} not found.")

        submission = await repo.create_kyc_submission(
            self._session, user_id=user_id, document_refs=document_refs
        )
        # Mark the user's KYC as SUBMITTED
        await repo.update_kyc_status(self._session, user_id, KYCStatus.SUBMITTED)
        return KYCSubmissionResponse.model_validate(submission)

    async def update_kyc_status(
        self,
        user_id: UUID,
        new_status: KYCStatus,
        provider_ref: str | None = None,
    ) -> UserProfile:
        user = await repo.get_user(self._session, user_id)
        if user is None:
            raise NotFound(f"User {user_id} not found.")

        old_status = user.kyc_status
        updated = await repo.update_kyc_status(
            self._session, user_id, new_status, provider_ref
        )
        assert updated is not None
        await events.publish(
            KYCStatusChanged(user_id=user_id, old_status=old_status, new_status=new_status)
        )
        return UserProfile.model_validate(updated)

    # ------------------------------------------------------------------
    # Sponsor ↔ Beneficiary links
    # ------------------------------------------------------------------

    async def list_beneficiaries(self, sponsor_id: UUID) -> list[UserProfile]:
        caller = await repo.get_user(self._session, sponsor_id)
        if caller is None or caller.role != UserRole.SPONSOR:
            raise PermissionDenied("Only sponsors can list beneficiaries.")
        users = await repo.list_beneficiaries(self._session, sponsor_id)
        return [UserProfile.model_validate(u) for u in users]

    async def link_beneficiary(
        self, sponsor_id: UUID, beneficiary_id: UUID
    ) -> SponsorBeneficiaryLinkResponse:
        # Validate both users exist and have the right roles
        sponsor = await repo.get_user(self._session, sponsor_id)
        if sponsor is None or sponsor.role != UserRole.SPONSOR:
            raise PermissionDenied("Only sponsors can create beneficiary links.")

        beneficiary = await repo.get_user(self._session, beneficiary_id)
        if beneficiary is None or beneficiary.role != UserRole.BENEFICIARY:
            raise NotFound("Beneficiary not found.")

        # Idempotent — reactivate if already exists but suspended
        existing = await repo.get_link(self._session, sponsor_id, beneficiary_id)
        if existing is not None:
            if existing.status == LinkStatus.ACTIVE:
                return SponsorBeneficiaryLinkResponse.model_validate(existing)
            updated = await repo.update_link_status(
                self._session, sponsor_id, beneficiary_id, LinkStatus.ACTIVE
            )
            assert updated is not None
            return SponsorBeneficiaryLinkResponse.model_validate(updated)

        link = await repo.create_link(self._session, sponsor_id, beneficiary_id)
        await events.publish(
            SponsorBeneficiaryLinked(sponsor_id=sponsor_id, beneficiary_id=beneficiary_id)
        )
        return SponsorBeneficiaryLinkResponse.model_validate(link)

    async def remove_beneficiary_link(
        self, sponsor_id: UUID, beneficiary_id: UUID
    ) -> None:
        caller = await repo.get_user(self._session, sponsor_id)
        if caller is None or caller.role != UserRole.SPONSOR:
            raise PermissionDenied("Only sponsors can remove beneficiary links.")
        link = await repo.get_link(self._session, sponsor_id, beneficiary_id)
        if link is None or link.status == LinkStatus.SUSPENDED:
            raise NotFound("Active link not found.")
        await repo.update_link_status(
            self._session, sponsor_id, beneficiary_id, LinkStatus.SUSPENDED
        )

    async def verify_sponsor_beneficiary_link(
        self, sponsor_id: UUID, beneficiary_id: UUID
    ) -> None:
        """Raise PermissionDenied if no active link exists. Called by other modules."""
        link = await repo.get_link(self._session, sponsor_id, beneficiary_id)
        if link is None or link.status != LinkStatus.ACTIVE:
            raise PermissionDenied(
                "No active sponsor-beneficiary link.",
                details={"sponsor_id": str(sponsor_id), "beneficiary_id": str(beneficiary_id)},
            )
