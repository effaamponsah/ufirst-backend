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

_SPONSOR_WALLET_CURRENCY = "GBP"

# Map ISO 3166-1 alpha-2 country codes to currency codes for beneficiary wallets.
_COUNTRY_CURRENCY: dict[str, str] = {
    "NG": "NGN",
    "GH": "GHS",
    "KE": "KES",
    "ZA": "ZAR",
    "UG": "UGX",
    "TZ": "TZS",
    "SN": "XOF",
    "CI": "XOF",
}
_DEFAULT_BENEFICIARY_CURRENCY = "NGN"


def _beneficiary_currency(country: str | None) -> str:
    if country is None:
        return _DEFAULT_BENEFICIARY_CURRENCY
    return _COUNTRY_CURRENCY.get(country.upper(), _DEFAULT_BENEFICIARY_CURRENCY)


async def on_user_created(event: UserCreated) -> None:
    """Auto-create a wallet for every new sponsor (GBP) or beneficiary (local currency)."""
    if event.role not in (UserRole.SPONSOR, UserRole.BENEFICIARY):
        return

    currency = (
        _SPONSOR_WALLET_CURRENCY
        if event.role == UserRole.SPONSOR
        else _beneficiary_currency(event.country)
    )

    try:
        from app.core.database import AsyncSessionFactory
        from app.modules.wallet.service import WalletService

        async with AsyncSessionFactory() as session:
            svc = WalletService(session)
            wallet = await svc.create_wallet(
                owner_id=event.user_id,
                currency=currency,
            )
            await session.commit()
            log.info(
                "Auto-created %s wallet %s for new %s %s",
                currency,
                wallet.id,
                event.role,
                event.user_id,
            )
    except Exception:
        log.exception(
            "Failed to auto-create wallet for %s %s", event.role, event.user_id
        )
