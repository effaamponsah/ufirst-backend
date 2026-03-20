"""
PaymentInitiationService — orchestrates the full payment initiation flow.

Steps per PLAN.md §3.3:
  1. Verify sponsor-beneficiary link (if funding a beneficiary's wallet)
  2. Compliance screen (Phase 6 stub — always PASS)
  3. Lock FX rate (hardcoded table; Phase 4 will add live provider + Redis cache)
  4. Create FundingTransfer record (INITIATED)
  5. Call aggregator.initiate() → payment_id + auth_link
  6. Create OpenBankingPayment / CardPayment record
  7. Transition FundingTransfer → AWAITING_AUTHORIZATION
  8. Publish FundingInitiated event
  9. Return (funding_transfer_id, auth_link)

Routing (§3.8):
  - PaymentMethod.OPEN_BANKING → TrueLayerClient
  - PaymentMethod.CARD         → StripeClient
  - ACH / MOBILE_MONEY         → no aggregator call (future-phase stubs)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.exceptions import AggregatorError
from app.modules.identity.service import IdentityService
from app.modules.wallet import repository as repo
from app.modules.wallet.events import FundingInitiated
from app.modules.wallet.models import PaymentMethod, PaymentState
from app.modules.wallet.openbanking.adapter import PaymentAdapter, get_adapter
from app.modules.wallet.service import WalletService

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FX rate table (Phase 3 stub)
# Phase 4: replace with live market-data provider + Redis TTL cache
# ---------------------------------------------------------------------------

_FX_RATES: dict[tuple[str, str], Decimal] = {
    ("GBP", "NGN"): Decimal("1950.00"),
    ("GBP", "GHS"): Decimal("17.50"),
    ("GBP", "EUR"): Decimal("1.17"),
    ("GBP", "USD"): Decimal("1.27"),
    ("EUR", "NGN"): Decimal("1680.00"),
    ("EUR", "USD"): Decimal("1.09"),
    ("USD", "NGN"): Decimal("1560.00"),
}

_FX_LOCK_SECONDS = 120  # rate locked for 2 minutes per payment


def _get_fx_rate(source: str, dest: str) -> Decimal:
    if source.upper() == dest.upper():
        return Decimal("1.0")
    key = (source.upper(), dest.upper())
    if key in _FX_RATES:
        return _FX_RATES[key]
    rev = (dest.upper(), source.upper())
    if rev in _FX_RATES:
        return (Decimal("1") / _FX_RATES[rev]).quantize(Decimal("0.000001"))
    log.warning("No FX rate for %s→%s; defaulting to 1:1", source, dest)
    return Decimal("1.0")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PaymentInitiationService:
    def __init__(
        self,
        session: AsyncSession,
        identity_service: IdentityService,
    ) -> None:
        self._session = session
        self._identity_svc = identity_service
        self._wallet_svc = WalletService(session)

    def _adapter_for(self, payment_method: PaymentMethod) -> PaymentAdapter:
        if payment_method == PaymentMethod.CARD:
            return get_adapter("card")
        return get_adapter("open_banking")

    def _aggregator_name(self, payment_method: PaymentMethod) -> str:
        from app.config import settings

        if payment_method == PaymentMethod.CARD:
            return "stripe"
        return settings.openbanking_provider.lower()

    def _redirect_uri(self, payment_method: PaymentMethod) -> str:
        from app.config import settings

        if payment_method == PaymentMethod.CARD:
            return (
                settings.stripe_redirect_uri
                or f"{settings.app_base_url}/api/v1/webhooks/stripe/payment-status"
            )
        return (
            settings.truelayer_redirect_uri
            or f"{settings.app_base_url}/api/v1/webhooks/openbanking/payment-status"
        )

    async def _get_existing_auth_link(
        self, transfer_id: UUID, payment_method: PaymentMethod
    ) -> str | None:
        """Return cached auth_link if the aggregator was already called for this transfer."""
        if payment_method == PaymentMethod.CARD:
            cp = await repo.get_card_payment_by_transfer_id(self._session, transfer_id)
            return cp.auth_link if cp else None
        ob = await repo.get_open_banking_payment_by_transfer_id(
            self._session, transfer_id
        )
        return ob.auth_link if ob else None

    async def initiate_payment(
        self,
        *,
        sponsor_id: UUID,
        wallet_id: UUID,
        payment_method: PaymentMethod,
        source_amount: int,
        source_currency: str,
        dest_currency: str,
        idempotency_key: str,
        beneficiary_wallet_id: UUID | None = None,
        bank_account_id: str | None = None,
    ) -> tuple[UUID, str | None]:
        """
        Run the full payment initiation flow.

        Returns (funding_transfer_id, auth_link).
        auth_link is None for non-redirect methods (ACH / mobile money stubs).
        """
        # 1. Verify sponsor-beneficiary link when funding a beneficiary wallet
        if beneficiary_wallet_id is not None:
            bene_wallet = await self._wallet_svc.get_wallet(beneficiary_wallet_id)
            await self._identity_svc.verify_sponsor_beneficiary_link(
                sponsor_id, bene_wallet.owner_id
            )
            actual_wallet_id = beneficiary_wallet_id
        else:
            actual_wallet_id = wallet_id

        # 2. Compliance screen (Phase 6 stub)
        await self._screen_funding(sponsor_id, source_amount, source_currency)

        # 3. Lock FX rate
        fx_rate = _get_fx_rate(source_currency, dest_currency)
        fx_rate_locked_until = datetime.now(timezone.utc) + timedelta(
            seconds=_FX_LOCK_SECONDS
        )
        dest_amount = int(source_amount * fx_rate)

        # 4. Create FundingTransfer (idempotent — returns existing on retry)
        transfer_response = await self._wallet_svc.initiate_funding(
            wallet_id=actual_wallet_id,
            sponsor_id=sponsor_id,
            payment_method=payment_method,
            source_amount=source_amount,
            source_currency=source_currency,
            dest_amount=dest_amount,
            dest_currency=dest_currency,
            fx_rate=fx_rate,
            fx_rate_locked_until=fx_rate_locked_until,
            fee_amount=0,
            idempotency_key=idempotency_key,
        )

        # Idempotent: aggregator already called on a previous attempt
        cached_link = await self._get_existing_auth_link(
            transfer_response.id, payment_method
        )
        if cached_link is not None:
            return transfer_response.id, cached_link

        # Stubs for ACH / mobile money — no aggregator in Phase 3
        if payment_method not in (PaymentMethod.OPEN_BANKING, PaymentMethod.CARD):
            await self._wallet_svc.advance_funding_state(
                transfer_response.id,
                new_state=PaymentState.AWAITING_AUTHORIZATION,
            )
            await events.publish(
                FundingInitiated(
                    funding_transfer_id=transfer_response.id,
                    sponsor_id=sponsor_id,
                    payment_method=payment_method.value,
                    amount=source_amount,
                    currency=source_currency,
                )
            )
            return transfer_response.id, None

        # 5. Call aggregator
        adapter = self._adapter_for(payment_method)
        try:
            result = await adapter.initiate(
                amount=source_amount,
                currency=source_currency,
                beneficiary_name="U-FirstSupport",
                idempotency_key=idempotency_key,
                redirect_uri=self._redirect_uri(payment_method),
                bank_account_id=bank_account_id,
            )
        except AggregatorError:
            # Transfer stays in INITIATED — client retries with same idempotency key
            raise

        # 6. Persist aggregator record
        if payment_method == PaymentMethod.CARD:
            await repo.create_card_payment(
                self._session,
                funding_transfer_id=transfer_response.id,
                stripe_payment_intent_id=result.payment_id,
                auth_link=result.auth_link,
            )
        else:
            await repo.create_open_banking_payment(
                self._session,
                funding_transfer_id=transfer_response.id,
                aggregator=self._aggregator_name(payment_method),
                aggregator_payment_id=result.payment_id,
                auth_link=result.auth_link,
            )

        # 7. Transition → AWAITING_AUTHORIZATION
        await self._wallet_svc.advance_funding_state(
            transfer_response.id,
            new_state=PaymentState.AWAITING_AUTHORIZATION,
            external_payment_ref=result.payment_id,
        )

        # 8. Publish event
        await events.publish(
            FundingInitiated(
                funding_transfer_id=transfer_response.id,
                sponsor_id=sponsor_id,
                payment_method=payment_method.value,
                amount=source_amount,
                currency=source_currency,
            )
        )

        # 9. Return auth_link
        return transfer_response.id, result.auth_link

    async def _screen_funding(
        self,
        sponsor_id: UUID,
        amount: int,
        currency: str,
    ) -> None:
        """Phase 6 stub — ComplianceService.screen_funding() plugs in here."""
        pass
