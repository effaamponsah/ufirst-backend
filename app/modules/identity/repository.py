from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import (
    KYCStatus,
    KYCSubmission,
    KYCSubmissionStatus,
    LinkStatus,
    SponsorBeneficiaryLink,
    User,
    UserRole,
)


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    email: str | None,
    role: UserRole,
    phone: str | None = None,
    full_name: str | None = None,
    country: str | None = None,
    beneficiary_relationship: str | None = None,
) -> User:
    user = User(
        id=user_id,
        email=email,
        role=role,
        phone=phone,
        full_name=full_name,
        country=country,
        beneficiary_relationship=beneficiary_relationship,
        kyc_status=KYCStatus.PENDING,
    )
    session.add(user)
    await session.flush()
    return user


async def upsert_profile(
    session: AsyncSession,
    user_id: UUID,
    *,
    email: str | None,
    country: str | None,
    phone: str | None,
    full_name: str | None,
    beneficiary_relationship: str | None,
) -> User | None:
    user = await get_user(session, user_id)
    if user is None:
        return None
    if email is not None:
        user.email = email
    if country is not None:
        user.country = country
    if phone is not None:
        user.phone = phone
    if full_name is not None:
        user.full_name = full_name
    if beneficiary_relationship is not None:
        user.beneficiary_relationship = beneficiary_relationship
    await session.flush()
    return user


async def update_kyc_status(
    session: AsyncSession,
    user_id: UUID,
    status: KYCStatus,
    provider_ref: str | None = None,
) -> User | None:
    user = await get_user(session, user_id)
    if user is None:
        return None
    user.kyc_status = status
    await session.flush()
    return user


async def create_kyc_submission(
    session: AsyncSession,
    *,
    user_id: UUID,
    document_refs: str | None = None,
) -> KYCSubmission:
    submission = KYCSubmission(
        user_id=user_id,
        status=KYCSubmissionStatus.PENDING,
        document_refs=document_refs,
    )
    session.add(submission)
    await session.flush()
    return submission


async def update_kyc_submission(
    session: AsyncSession,
    *,
    submission_id: UUID,
    status: KYCSubmissionStatus,
    provider_ref: str | None = None,
    reviewer_notes: str | None = None,
) -> KYCSubmission | None:
    result = await session.execute(
        select(KYCSubmission).where(KYCSubmission.id == submission_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return None
    sub.status = status
    if provider_ref is not None:
        sub.provider_ref = provider_ref
    if reviewer_notes is not None:
        sub.reviewer_notes = reviewer_notes
    await session.flush()
    return sub


async def get_link(
    session: AsyncSession, sponsor_id: UUID, beneficiary_id: UUID
) -> SponsorBeneficiaryLink | None:
    result = await session.execute(
        select(SponsorBeneficiaryLink).where(
            SponsorBeneficiaryLink.sponsor_id == sponsor_id,
            SponsorBeneficiaryLink.beneficiary_id == beneficiary_id,
        )
    )
    return result.scalar_one_or_none()


async def create_link(
    session: AsyncSession, sponsor_id: UUID, beneficiary_id: UUID
) -> SponsorBeneficiaryLink:
    link = SponsorBeneficiaryLink(
        sponsor_id=sponsor_id,
        beneficiary_id=beneficiary_id,
        status=LinkStatus.ACTIVE,
    )
    session.add(link)
    await session.flush()
    return link


async def update_link_status(
    session: AsyncSession,
    sponsor_id: UUID,
    beneficiary_id: UUID,
    status: LinkStatus,
) -> SponsorBeneficiaryLink | None:
    link = await get_link(session, sponsor_id, beneficiary_id)
    if link is None:
        return None
    link.status = status
    await session.flush()
    return link


async def list_beneficiaries(session: AsyncSession, sponsor_id: UUID) -> list[User]:
    result = await session.execute(
        select(User)
        .join(
            SponsorBeneficiaryLink,
            SponsorBeneficiaryLink.beneficiary_id == User.id,
        )
        .where(
            SponsorBeneficiaryLink.sponsor_id == sponsor_id,
            SponsorBeneficiaryLink.status == LinkStatus.ACTIVE,
        )
    )
    return list(result.scalars().all())
