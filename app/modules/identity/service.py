"""
IdentityService — public interface for the identity module.

All other modules MUST use this service to read user or link data.
Never import from identity.repository or identity.models directly
from outside this module.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.exceptions import NotFound, PermissionDenied
from app.modules.identity import repository as repo
from app.modules.identity.events import KYCStatusChanged, SponsorBeneficiaryLinked, UserCreated
from app.modules.identity.models import KYCStatus, LinkStatus, UserRole
from app.modules.identity.schemas import (
    CompleteProfileRequest,
    CreateBeneficiaryRequest,
    KYCSubmissionResponse,
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

    async def get_or_create_user(
        self,
        supabase_uid: UUID,
        *,
        email: str,
        role: str,
    ) -> UserProfile:
        """
        Return the identity record for this Supabase UID, creating a skeleton
        record from JWT claims if one does not yet exist.

        Called by the lazy-provisioning middleware on every authenticated
        request, and by route handlers that need the current user's DB record.
        """
        existing = await repo.get_user(self._session, supabase_uid)
        if existing is not None:
            return UserProfile.model_validate(existing)

        try:
            role_enum = UserRole(role)
        except ValueError:
            raise PermissionDenied(f"Unknown role: '{role}'")

        user = await repo.create_user(
            self._session,
            user_id=supabase_uid,
            email=email or None,  # "" → NULL; phone-only JWTs and dev tokens have no email
            role=role_enum,
        )
        await events.publish(UserCreated(user_id=user.id, role=user.role, email=user.email))
        return UserProfile.model_validate(user)

    async def complete_profile(
        self,
        user_id: UUID,
        data: CompleteProfileRequest,
    ) -> UserProfile:
        """
        Upsert profile fields (country, phone, full_name, beneficiary_relationship)
        onto the skeleton record created by the auth middleware.
        """
        user = await repo.upsert_profile(
            self._session,
            user_id,
            email=data.email,
            country=data.country,
            phone=data.phone,
            full_name=data.full_name,
            beneficiary_relationship=data.beneficiary_relationship,
        )
        if user is None:
            raise NotFound(f"User {user_id} not found.")
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

    async def create_beneficiary(
        self, sponsor_id: UUID, data: CreateBeneficiaryRequest
    ) -> UserProfile:
        """
        Sponsor creates a beneficiary account and links it to themselves atomically.
        A new UUID is generated for the beneficiary — they can use the card immediately
        without needing a Supabase login.
        """
        sponsor = await repo.get_user(self._session, sponsor_id)
        if sponsor is None or sponsor.role != UserRole.SPONSOR:
            raise PermissionDenied("Only sponsors can create beneficiaries.")

        beneficiary = await repo.create_user(
            self._session,
            user_id=uuid4(),
            email=data.email or None,
            role=UserRole.BENEFICIARY,
            phone=data.phone,
            full_name=data.full_name,
            country=data.country,
            beneficiary_relationship=data.beneficiary_relationship,
        )
        await repo.create_link(self._session, sponsor_id, beneficiary.id)
        await events.publish(UserCreated(user_id=beneficiary.id, role=beneficiary.role, email=beneficiary.email))
        await events.publish(SponsorBeneficiaryLinked(sponsor_id=sponsor_id, beneficiary_id=beneficiary.id))
        return UserProfile.model_validate(beneficiary)

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
