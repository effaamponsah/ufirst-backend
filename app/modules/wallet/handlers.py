"""
Wallet event handlers — subscribed at application startup in app/main.py.

Handlers must not raise: log and swallow so they never affect the caller's
transaction (per the event bus contract in app/core/events.py).
"""

from __future__ import annotations

import logging

from app.modules.identity.events import UserCreated
from app.modules.identity.models import UserRole

log = logging.getLogger(__name__)

# Default currency for new sponsor wallets. Sponsors fund in GBP; beneficiary
# wallets are created separately when a card is issued (in their local currency).
_SPONSOR_WALLET_CURRENCY = "GBP"


async def on_user_created(event: UserCreated) -> None:
    """Auto-create a GBP wallet for every new sponsor."""
    if event.role != UserRole.SPONSOR:
        return

    try:
        from app.core.database import AsyncSessionFactory
        from app.modules.wallet.service import WalletService

        async with AsyncSessionFactory() as session:
            svc = WalletService(session)
            wallet = await svc.create_wallet(
                owner_id=event.user_id,
                currency=_SPONSOR_WALLET_CURRENCY,
            )
            await session.commit()
            log.info(
                "Auto-created wallet %s for new sponsor %s",
                wallet.id,
                event.user_id,
            )
    except Exception:
        log.exception(
            "Failed to auto-create wallet for sponsor %s", event.user_id
        )
