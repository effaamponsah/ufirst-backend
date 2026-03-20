"""
CardService — public interface for the card module.

All other modules MUST use this service. Never import from card.repository
or card.models directly from outside this module.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.exceptions import InvalidStateTransition, NotFound, ValidationError
from app.modules.card import repository as repo
from app.modules.card.events import (
    CardActivated,
    CardCancelled,
    CardFrozen,
    CardIssued,
    CardSpendingControlsUpdated,
    CardUnfrozen,
)
from app.modules.card.models import CardEventType, CardStatus
from app.modules.card.processor.client import get_processor
from app.modules.card.schemas import CardResponse, SpendingControls

# Default card program — configurable per environment
_DEFAULT_CARD_PROGRAM_ID = "ufirst_prepaid_v1"

# Valid status transitions
_VALID_TRANSITIONS: dict[CardStatus, set[CardStatus]] = {
    CardStatus.PENDING: {CardStatus.ACTIVE, CardStatus.CANCELLED},
    CardStatus.ACTIVE: {CardStatus.FROZEN, CardStatus.CANCELLED},
    CardStatus.FROZEN: {CardStatus.ACTIVE, CardStatus.CANCELLED},
    CardStatus.CANCELLED: set(),
}


class CardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue_card(
        self,
        *,
        wallet_id: UUID,
        beneficiary_id: UUID,
        issued_by: UUID,
        spending_controls: SpendingControls | None = None,
        card_program_id: str = _DEFAULT_CARD_PROGRAM_ID,
    ) -> CardResponse:
        """
        Issue a prepaid card for a beneficiary.

        Calls the card processor to obtain a processor token (never a raw PAN).
        The sponsor (issued_by) must have verified the sponsor↔beneficiary link
        before calling this method.
        """
        # One active card per wallet — reject if already issued
        existing = await repo.get_card_for_wallet(self._session, wallet_id)
        if existing is not None:
            raise ValidationError(
                "An active card already exists for this wallet.",
                details={"wallet_id": str(wallet_id), "card_id": str(existing.id)},
            )

        processor = get_processor()
        token_result = await processor.issue_card(
            beneficiary_id=str(beneficiary_id),
            wallet_id=str(wallet_id),
            card_program_id=card_program_id,
        )

        controls_dict = spending_controls.model_dump(exclude_none=True) if spending_controls else None

        card = await repo.create_card(
            self._session,
            wallet_id=wallet_id,
            owner_id=beneficiary_id,
            processor_token=token_result.token,
            card_program_id=token_result.card_program_id,
            issued_at=token_result.issued_at,
            expires_at=token_result.expires_at,
            spending_controls=controls_dict,
        )

        await repo.create_card_event(
            self._session,
            card_id=card.id,
            event_type=CardEventType.ISSUED,
            actor_id=issued_by,
        )

        await events.publish(
            CardIssued(card_id=card.id, wallet_id=wallet_id, owner_id=beneficiary_id)
        )

        return CardResponse.model_validate(card)

    async def activate_card(
        self,
        card_id: UUID,
        *,
        actor_id: UUID,
    ) -> CardResponse:
        """
        Transition a card from PENDING → ACTIVE.

        Called when UP Nigeria confirms the physical card has been dispatched.
        Trigger points:
          - Webhook from UP Nigeria  →  POST /webhooks/card-processor/dispatched
          - Manual ops action        →  POST /cards/{id}/activate
        """
        card = await repo.get_card(self._session, card_id)
        if card is None:
            raise NotFound(f"Card {card_id} not found.")
        if CardStatus.ACTIVE not in _VALID_TRANSITIONS.get(card.status, set()):
            raise InvalidStateTransition(
                f"Cannot activate a card in status {card.status}.",
                details={"card_id": str(card_id)},
            )

        processor = get_processor()
        await processor.activate_card(processor_token=card.processor_token)

        card = await repo.update_card_status(self._session, card, new_status=CardStatus.ACTIVE)
        await repo.create_card_event(
            self._session,
            card_id=card_id,
            event_type=CardEventType.ACTIVATED,
            actor_id=actor_id,
        )

        await events.publish(CardActivated(card_id=card_id, owner_id=card.owner_id))
        return CardResponse.model_validate(card)

    async def get_card(self, card_id: UUID) -> CardResponse:
        card = await repo.get_card(self._session, card_id)
        if card is None:
            raise NotFound(f"Card {card_id} not found.")
        return CardResponse.model_validate(card)

    async def get_card_for_wallet(self, wallet_id: UUID) -> CardResponse | None:
        card = await repo.get_card_for_wallet(self._session, wallet_id)
        if card is None:
            return None
        return CardResponse.model_validate(card)

    async def freeze_card(
        self,
        card_id: UUID,
        *,
        actor_id: UUID,
        reason: str | None = None,
    ) -> CardResponse:
        card = await repo.get_card(self._session, card_id)
        if card is None:
            raise NotFound(f"Card {card_id} not found.")
        if CardStatus.FROZEN not in _VALID_TRANSITIONS.get(card.status, set()):
            raise InvalidStateTransition(
                f"Cannot freeze a card in status {card.status}.",
                details={"card_id": str(card_id)},
            )

        processor = get_processor()
        await processor.update_card_status(
            processor_token=card.processor_token, status="frozen"
        )

        card = await repo.update_card_status(self._session, card, new_status=CardStatus.FROZEN)
        await repo.create_card_event(
            self._session,
            card_id=card_id,
            event_type=CardEventType.FROZEN,
            actor_id=actor_id,
            reason=reason,
        )

        await events.publish(
            CardFrozen(card_id=card_id, owner_id=card.owner_id, reason=reason)
        )
        return CardResponse.model_validate(card)

    async def unfreeze_card(
        self,
        card_id: UUID,
        *,
        actor_id: UUID,
    ) -> CardResponse:
        card = await repo.get_card(self._session, card_id)
        if card is None:
            raise NotFound(f"Card {card_id} not found.")
        if CardStatus.ACTIVE not in _VALID_TRANSITIONS.get(card.status, set()):
            raise InvalidStateTransition(
                f"Cannot unfreeze a card in status {card.status}.",
                details={"card_id": str(card_id)},
            )

        processor = get_processor()
        await processor.update_card_status(
            processor_token=card.processor_token, status="active"
        )

        card = await repo.update_card_status(self._session, card, new_status=CardStatus.ACTIVE)
        await repo.create_card_event(
            self._session,
            card_id=card_id,
            event_type=CardEventType.UNFROZEN,
            actor_id=actor_id,
        )

        await events.publish(CardUnfrozen(card_id=card_id, owner_id=card.owner_id))
        return CardResponse.model_validate(card)

    async def cancel_card(
        self,
        card_id: UUID,
        *,
        actor_id: UUID,
        reason: str | None = None,
    ) -> CardResponse:
        card = await repo.get_card(self._session, card_id)
        if card is None:
            raise NotFound(f"Card {card_id} not found.")
        if CardStatus.CANCELLED not in _VALID_TRANSITIONS.get(card.status, set()):
            raise InvalidStateTransition(
                f"Cannot cancel a card in status {card.status}.",
                details={"card_id": str(card_id)},
            )

        processor = get_processor()
        await processor.update_card_status(
            processor_token=card.processor_token, status="cancelled"
        )

        card = await repo.update_card_status(self._session, card, new_status=CardStatus.CANCELLED)
        await repo.create_card_event(
            self._session,
            card_id=card_id,
            event_type=CardEventType.CANCELLED,
            actor_id=actor_id,
            reason=reason,
        )

        await events.publish(
            CardCancelled(card_id=card_id, owner_id=card.owner_id, reason=reason)
        )
        return CardResponse.model_validate(card)

    async def update_spending_controls(
        self,
        card_id: UUID,
        *,
        controls: SpendingControls,
        actor_id: UUID,
    ) -> CardResponse:
        card = await repo.get_card(self._session, card_id)
        if card is None:
            raise NotFound(f"Card {card_id} not found.")
        if card.status == CardStatus.CANCELLED:
            raise InvalidStateTransition(
                "Cannot update spending controls on a cancelled card.",
                details={"card_id": str(card_id)},
            )

        controls_dict = controls.model_dump(exclude_none=True)

        processor = get_processor()
        await processor.update_spending_controls(
            processor_token=card.processor_token, controls=controls_dict
        )

        card = await repo.update_card_spending_controls(
            self._session, card, controls=controls_dict
        )
        await repo.create_card_event(
            self._session,
            card_id=card_id,
            event_type=CardEventType.CONTROLS_UPDATED,
            actor_id=actor_id,
            event_metadata=controls_dict,
        )

        await events.publish(
            CardSpendingControlsUpdated(card_id=card_id, owner_id=card.owner_id)
        )
        return CardResponse.model_validate(card)
