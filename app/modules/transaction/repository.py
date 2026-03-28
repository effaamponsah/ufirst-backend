from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.transaction.models import (
    Authorization,
    AuthorizationStatus,
    Clearing,
    Dispute,
    DisputeStatus,
)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def get_authorization(
    session: AsyncSession, authorization_id: UUID
) -> Authorization | None:
    result = await session.execute(
        select(Authorization).where(Authorization.id == authorization_id)
    )
    return result.scalar_one_or_none()


async def get_authorization_by_processor_ref(
    session: AsyncSession, processor_auth_ref: str
) -> Authorization | None:
    result = await session.execute(
        select(Authorization).where(
            Authorization.processor_auth_ref == processor_auth_ref
        )
    )
    return result.scalar_one_or_none()


async def create_authorization(
    session: AsyncSession,
    *,
    card_id: UUID,
    wallet_id: UUID,
    merchant_name: str,
    merchant_category_code: str | None,
    amount: int,
    currency: str,
    status: AuthorizationStatus,
    processor_auth_ref: str,
    decline_reason: str | None = None,
) -> Authorization:
    auth = Authorization(
        card_id=card_id,
        wallet_id=wallet_id,
        merchant_name=merchant_name,
        merchant_category_code=merchant_category_code,
        amount=amount,
        currency=currency,
        status=status,
        processor_auth_ref=processor_auth_ref,
        decline_reason=decline_reason,
    )
    session.add(auth)
    await session.flush()
    await session.refresh(auth)
    return auth


async def update_authorization_status(
    session: AsyncSession,
    auth: Authorization,
    *,
    new_status: AuthorizationStatus,
) -> Authorization:
    auth.status = new_status
    session.add(auth)
    await session.flush()
    await session.refresh(auth)
    return auth


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------


async def create_clearing(
    session: AsyncSession,
    *,
    authorization_id: UUID,
    cleared_amount: int,
    cleared_currency: str,
    processor_clearing_ref: str | None = None,
) -> Clearing:
    clearing = Clearing(
        authorization_id=authorization_id,
        cleared_amount=cleared_amount,
        cleared_currency=cleared_currency,
        processor_clearing_ref=processor_clearing_ref,
    )
    session.add(clearing)
    await session.flush()
    await session.refresh(clearing)
    return clearing


# ---------------------------------------------------------------------------
# Daily spending
# ---------------------------------------------------------------------------


async def get_daily_authorized_total(
    session: AsyncSession, card_id: UUID
) -> int:
    """
    Sum of amount for AUTHORIZED status authorizations for this card today (UTC).
    Returns 0 if none.
    """
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    result = await session.execute(
        select(func.coalesce(func.sum(Authorization.amount), 0)).where(
            Authorization.card_id == card_id,
            Authorization.status == AuthorizationStatus.AUTHORIZED,
            Authorization.authorized_at >= today_start,
        )
    )
    total = result.scalar_one()
    return int(total)


# ---------------------------------------------------------------------------
# Dispute
# ---------------------------------------------------------------------------


async def create_dispute(
    session: AsyncSession,
    *,
    authorization_id: UUID,
    reason: str,
) -> Dispute:
    dispute = Dispute(
        authorization_id=authorization_id,
        reason=reason,
        status=DisputeStatus.OPEN,
    )
    session.add(dispute)
    await session.flush()
    await session.refresh(dispute)
    return dispute


async def get_dispute(session: AsyncSession, dispute_id: UUID) -> Dispute | None:
    result = await session.execute(
        select(Dispute).where(Dispute.id == dispute_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def list_authorizations_for_wallet(
    session: AsyncSession,
    wallet_id: UUID,
    *,
    limit: int,
    offset: int,
) -> list[Authorization]:
    result = await session.execute(
        select(Authorization)
        .where(Authorization.wallet_id == wallet_id)
        .order_by(Authorization.authorized_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
