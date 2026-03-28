from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.card.models import Card, CardEvent, CardEventType, CardStatus


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


async def get_card(session: AsyncSession, card_id: UUID) -> Card | None:
    result = await session.execute(select(Card).where(Card.id == card_id))
    return result.scalar_one_or_none()


async def get_card_by_owner(session: AsyncSession, owner_id: UUID) -> Card | None:
    """Return the most recently issued non-cancelled card for a beneficiary."""
    result = await session.execute(
        select(Card)
        .where(Card.owner_id == owner_id, Card.status != CardStatus.CANCELLED)
        .order_by(Card.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_card_by_processor_token(
    session: AsyncSession, processor_token: str
) -> Card | None:
    result = await session.execute(
        select(Card).where(Card.processor_token == processor_token)
    )
    return result.scalar_one_or_none()


async def get_card_for_wallet(session: AsyncSession, wallet_id: UUID) -> Card | None:
    result = await session.execute(
        select(Card)
        .where(Card.wallet_id == wallet_id, Card.status != CardStatus.CANCELLED)
        .order_by(Card.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def create_card(
    session: AsyncSession,
    *,
    wallet_id: UUID,
    owner_id: UUID,
    processor_token: str,
    card_program_id: str,
    issued_at: datetime,
    expires_at: datetime,
    spending_controls: dict | None = None,  # type: ignore[type-arg]
) -> Card:
    card = Card(
        wallet_id=wallet_id,
        owner_id=owner_id,
        processor_token=processor_token,
        card_program_id=card_program_id,
        status=CardStatus.PENDING,
        issued_at=issued_at,
        expires_at=expires_at,
        spending_controls=spending_controls,
    )
    session.add(card)
    await session.flush()
    await session.refresh(card)
    return card


async def update_card_status(
    session: AsyncSession,
    card: Card,
    *,
    new_status: CardStatus,
) -> Card:
    card.status = new_status
    session.add(card)
    await session.flush()
    await session.refresh(card)
    return card


async def update_card_spending_controls(
    session: AsyncSession,
    card: Card,
    *,
    controls: dict,  # type: ignore[type-arg]
) -> Card:
    card.spending_controls = controls
    session.add(card)
    await session.flush()
    await session.refresh(card)
    return card


# ---------------------------------------------------------------------------
# Card events (append-only)
# ---------------------------------------------------------------------------


async def create_card_event(
    session: AsyncSession,
    *,
    card_id: UUID,
    event_type: CardEventType,
    actor_id: UUID | None = None,
    reason: str | None = None,
    event_metadata: dict | None = None,  # type: ignore[type-arg]
) -> CardEvent:
    event = CardEvent(
        card_id=card_id,
        event_type=event_type,
        actor_id=actor_id,
        reason=reason,
        event_metadata=event_metadata,
    )
    session.add(event)
    await session.flush()
    return event


async def list_card_events(
    session: AsyncSession,
    card_id: UUID,
) -> list[CardEvent]:
    result = await session.execute(
        select(CardEvent)
        .where(CardEvent.card_id == card_id)
        .order_by(CardEvent.created_at.asc())
    )
    return list(result.scalars().all())
