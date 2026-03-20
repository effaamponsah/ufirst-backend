"""
Card processor client — abstract interface + dev stub + factory.

The factory get_processor() returns the right implementation based on config:

  - UP_NIGERIA_API_KEY set  →  UPNigeriaClient   (production)
  - UP_NIGERIA_API_KEY unset →  DevCardProcessorClient  (dev / CI)

Nothing else in the codebase needs to change when UP Nigeria is wired in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass
class ProcessorToken:
    token: str
    card_program_id: str
    issued_at: datetime
    expires_at: datetime


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class CardProcessorClient(ABC):
    """
    All card processor integrations implement this interface.

    Lifecycle for a physical prepaid card:
        issue_card()      → card ordered; physical card printed & mailed by processor
        activate_card()   → card confirmed dispatched / beneficiary activates it
        update_card_status("frozen" | "cancelled")
        update_spending_controls(...)
    """

    @abstractmethod
    async def issue_card(
        self,
        *,
        beneficiary_id: str,
        wallet_id: str,
        card_program_id: str,
    ) -> ProcessorToken:
        """
        Request card issuance.  The card starts in PENDING state — the
        physical card has been ordered but not yet delivered.
        Returns a processor token that uniquely identifies the card.
        Never returns a raw PAN.
        """
        ...

    @abstractmethod
    async def activate_card(self, *, processor_token: str) -> None:
        """
        Confirm the card is active and usable.

        In the UP Nigeria flow this is called when UP confirms the physical
        card has been dispatched to the beneficiary (either via webhook from
        UP Nigeria → POST /webhooks/card-processor/dispatched, or manually
        by ops via POST /cards/{id}/activate).
        """
        ...

    @abstractmethod
    async def update_card_status(
        self,
        *,
        processor_token: str,
        status: str,  # "frozen" | "cancelled"
    ) -> None: ...

    @abstractmethod
    async def update_spending_controls(
        self,
        *,
        processor_token: str,
        controls: dict,  # type: ignore[type-arg]
    ) -> None: ...


# ---------------------------------------------------------------------------
# Dev stub — used when UP_NIGERIA_API_KEY is not configured
# ---------------------------------------------------------------------------


class DevCardProcessorClient(CardProcessorClient):
    """
    No-op client for local dev and CI.

    Returns deterministic fake tokens so the full card lifecycle can be
    exercised without hitting any external API.
    """

    async def issue_card(
        self,
        *,
        beneficiary_id: str,
        wallet_id: str,
        card_program_id: str,
    ) -> ProcessorToken:
        import hashlib

        token = "dev_tok_" + hashlib.sha256(
            f"{beneficiary_id}:{wallet_id}".encode()
        ).hexdigest()[:16]
        now = datetime.now(tz=timezone.utc)
        return ProcessorToken(
            token=token,
            card_program_id=card_program_id,
            issued_at=now,
            expires_at=now + timedelta(days=1460),  # 4 years
        )

    async def activate_card(self, *, processor_token: str) -> None:
        pass  # no-op in dev — activation is immediate

    async def update_card_status(self, *, processor_token: str, status: str) -> None:
        pass  # no-op in dev

    async def update_spending_controls(
        self, *, processor_token: str, controls: dict  # type: ignore[type-arg]
    ) -> None:
        pass  # no-op in dev


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_processor: CardProcessorClient | None = None


def get_processor() -> CardProcessorClient:
    """
    Return a shared processor client instance.

    Selects the implementation based on config:
      - UP_NIGERIA_API_KEY present → UPNigeriaClient
      - otherwise                  → DevCardProcessorClient
    """
    global _processor
    if _processor is None:
        from app.config import settings

        if getattr(settings, "up_nigeria_api_key", None):
            from app.modules.card.processor.up_nigeria import UPNigeriaClient
            _processor = UPNigeriaClient()
        else:
            _processor = DevCardProcessorClient()
    return _processor
