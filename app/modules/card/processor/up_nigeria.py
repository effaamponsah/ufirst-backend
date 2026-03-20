"""
UP Nigeria (up-ng.com) card processor client.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO WIRE IN UP NIGERIA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1.  Obtain credentials from UP Nigeria (up-ng.com/developer):
      - API key / client credentials
      - Webhook signing secret
      - Card programme ID for U-FirstSupport prepaid cards

2.  Set the following environment variables (see .env.example):
      UP_NIGERIA_API_KEY=<your key>
      UP_NIGERIA_BASE_URL=https://api.up-ng.com   # confirm with UP Nigeria
      UP_NIGERIA_CARD_PROGRAM_ID=<programme id>
      UP_NIGERIA_WEBHOOK_SECRET=<signing secret>

3.  Implement each method below by replacing the `raise NotImplementedError`
    with a real HTTP call to the UP Nigeria API.  The rest of the codebase
    (CardService, routes, tests) does not need to change — the factory in
    client.py will automatically return this class once UP_NIGERIA_API_KEY
    is set.

4.  (Optional) Register the UP Nigeria webhook endpoint in main.py if UP
    Nigeria sends async status updates:
      POST /api/v1/webhooks/card-processor/dispatched  → activate_card()
      POST /api/v1/webhooks/card-processor/cancelled   → cancel_card()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import httpx

from app.modules.card.processor.client import CardProcessorClient, ProcessorToken

log = logging.getLogger(__name__)


class UPNigeriaClient(CardProcessorClient):
    """
    UP Nigeria prepaid card processor.

    Reads configuration from app.config.settings.  All methods are
    async and safe to call from FastAPI route handlers or Celery tasks.
    """

    def __init__(self) -> None:
        from app.config import settings

        self._api_key: str = settings.up_nigeria_api_key  # type: ignore[attr-defined]
        self._base_url: str = getattr(
            settings, "up_nigeria_base_url", "https://api.up-ng.com"
        )
        self._card_program_id: str = getattr(
            settings, "up_nigeria_card_program_id", "ufirst_prepaid_v1"
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    # ── UP NIGERIA INTEGRATION POINT ─────────────────────────────────────────
    # Each method below maps to one UP Nigeria API call.
    # Replace each `raise NotImplementedError` with the real implementation.
    # Refer to the UP Nigeria developer docs for endpoint paths and payloads.
    # ─────────────────────────────────────────────────────────────────────────

    async def issue_card(
        self,
        *,
        beneficiary_id: str,
        wallet_id: str,
        card_program_id: str,
    ) -> ProcessorToken:
        """
        Request physical prepaid card issuance from UP Nigeria.

        UP Nigeria will print and mail the card to the beneficiary's
        registered address.  The token returned here is used to manage
        the card in all subsequent API calls.

        Expected UP Nigeria endpoint (confirm in their docs):
            POST /v1/cards/issue
            {
                "programme_id": "<card_program_id>",
                "reference":    "<wallet_id>",          # your idempotency key
                "holder_id":    "<beneficiary_id>",
                ...
            }
        Response should include a token / card_id to store as processor_token.
        """
        # ── REPLACE THIS BLOCK ───────────────────────────────────────────────
        raise NotImplementedError(
            "UP Nigeria issue_card not yet implemented. "
            "See app/modules/card/processor/up_nigeria.py for instructions."
        )
        # ── END REPLACE ──────────────────────────────────────────────────────

        # Example skeleton (fill in real field names from UP Nigeria docs):
        # response = await self._client.post("/v1/cards/issue", json={
        #     "programme_id": card_program_id or self._card_program_id,
        #     "reference": wallet_id,
        #     "holder_id": beneficiary_id,
        # })
        # response.raise_for_status()
        # data = response.json()
        # now = datetime.now(tz=timezone.utc)
        # return ProcessorToken(
        #     token=data["card_token"],           # adjust field name
        #     card_program_id=card_program_id,
        #     issued_at=now,
        #     expires_at=now + timedelta(days=1460),
        # )

    async def activate_card(self, *, processor_token: str) -> None:
        """
        Confirm the card is active and usable at POS.

        This is called in two scenarios:
          A. UP Nigeria sends a webhook when the physical card is dispatched
             → POST /webhooks/card-processor/dispatched calls this method.
          B. Ops manually activates via POST /cards/{id}/activate (e.g. if
             no webhook is available from UP Nigeria).

        Expected UP Nigeria endpoint (confirm in their docs):
            POST /v1/cards/{processor_token}/activate
            or PUT /v1/cards/{processor_token}  { "status": "active" }
        """
        # ── REPLACE THIS BLOCK ───────────────────────────────────────────────
        raise NotImplementedError(
            "UP Nigeria activate_card not yet implemented. "
            "See app/modules/card/processor/up_nigeria.py for instructions."
        )
        # ── END REPLACE ──────────────────────────────────────────────────────

        # Example skeleton:
        # response = await self._client.post(f"/v1/cards/{processor_token}/activate")
        # response.raise_for_status()

    async def update_card_status(
        self, *, processor_token: str, status: str
    ) -> None:
        """
        Update the card status at UP Nigeria (freeze or cancel).

        status is "frozen" or "cancelled" — map to whatever UP Nigeria
        expects (e.g. "BLOCKED", "TERMINATED", etc.).

        Expected UP Nigeria endpoint (confirm in their docs):
            PUT /v1/cards/{processor_token}/status
            { "status": "<mapped_status>" }
        """
        # ── REPLACE THIS BLOCK ───────────────────────────────────────────────
        raise NotImplementedError(
            "UP Nigeria update_card_status not yet implemented. "
            "See app/modules/card/processor/up_nigeria.py for instructions."
        )
        # ── END REPLACE ──────────────────────────────────────────────────────

        # Example skeleton:
        # _status_map = {"frozen": "BLOCKED", "cancelled": "TERMINATED"}
        # response = await self._client.put(
        #     f"/v1/cards/{processor_token}/status",
        #     json={"status": _status_map[status]},
        # )
        # response.raise_for_status()

    async def update_spending_controls(
        self, *, processor_token: str, controls: dict  # type: ignore[type-arg]
    ) -> None:
        """
        Push updated spending controls to UP Nigeria.

        controls is a dict from SpendingControls.model_dump() — e.g.:
            {"daily_limit": 50000, "categories": ["grocery"]}

        Map these to whatever UP Nigeria's API accepts for limits/restrictions.

        Expected UP Nigeria endpoint (confirm in their docs):
            PUT /v1/cards/{processor_token}/controls
            { "daily_limit": ..., ... }
        """
        # ── REPLACE THIS BLOCK ───────────────────────────────────────────────
        raise NotImplementedError(
            "UP Nigeria update_spending_controls not yet implemented. "
            "See app/modules/card/processor/up_nigeria.py for instructions."
        )
        # ── END REPLACE ──────────────────────────────────────────────────────

        # Example skeleton:
        # response = await self._client.put(
        #     f"/v1/cards/{processor_token}/controls",
        #     json=controls,
        # )
        # response.raise_for_status()
