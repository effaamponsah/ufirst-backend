"""
Celery tasks for the wallet module.

  process_payment_webhook  — critical queue: process OB/Stripe payment events
  process_connect_callback — default  queue: process bank connection events
  poll_pending_payments    — beat (5 min): poll AWAITING_SETTLEMENT transfers
  expire_stale_authorizations — beat (1 min): expire 15-min-old AWAITING_AUTH
  warn_expiring_bank_consent  — beat (daily): notify sponsors of expiring consent
"""

from __future__ import annotations

import asyncio
import logging

from app.jobs.celery_app import celery_app

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine from a synchronous Celery task."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# process_payment_webhook
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.modules.wallet.tasks.process_payment_webhook",
    queue="critical",
    acks_late=True,
    max_retries=5,
    default_retry_delay=30,
)
def process_payment_webhook(payload: dict, aggregator: str = "open_banking") -> None:  # type: ignore[type-arg]
    """
    Process an OB or Stripe payment status webhook.

    On payment_executed:
      - Advance transfer through AUTHORIZING (if needed) → AWAITING_SETTLEMENT
      - Credit wallet → COMPLETED
    On payment_failed:
      - Advance transfer → FAILED
    On payment_pending:
      - Advance transfer towards AWAITING_SETTLEMENT if not already there
    """
    _run(_async_process_payment_webhook(payload, aggregator))


async def _async_process_payment_webhook(
    payload: dict,  # type: ignore[type-arg]
    aggregator: str,
) -> None:
    from datetime import timezone
    from datetime import datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.core import events
    from app.modules.wallet import repository as repo
    from app.modules.wallet.events import FundingFailed, FundingPaymentReceived
    from app.modules.wallet.models import PaymentState
    from app.modules.wallet.openbanking.adapter import get_adapter
    from app.modules.wallet.service import WalletService

    adapter = get_adapter(aggregator)
    raw_body = __import__("json").dumps(payload).encode()
    event = await adapter.parse_webhook(raw_body)

    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            ob_payment = await repo.get_open_banking_payment_by_aggregator_id(
                session, event.payment_id
            )
            if ob_payment is None:
                # May be a card payment — look up by intent_id for Stripe
                cp = await repo.get_card_payment_by_intent_id(
                    session, event.payment_id
                )
                if cp is None:
                    log.warning(
                        "Webhook for unknown payment_id=%s (aggregator=%s) — ignoring",
                        event.payment_id,
                        aggregator,
                    )
                    return
                transfer_id = cp.funding_transfer_id
                # Update card details from payment_failed path if available
            else:
                # Deduplication: same status already processed
                if (
                    ob_payment.bank_status == event.bank_status
                    and ob_payment.webhook_received_at is not None
                ):
                    log.info(
                        "Duplicate OB webhook for payment_id=%s status=%s — skipped",
                        event.payment_id,
                        event.bank_status,
                    )
                    return

                await repo.update_open_banking_payment(
                    session,
                    ob_payment,
                    bank_status=event.bank_status,
                    webhook_received_at=datetime.now(timezone.utc),
                    failure_reason=event.failure_reason,
                )
                transfer_id = ob_payment.funding_transfer_id

            svc = WalletService(session)
            transfer = await repo.get_funding_transfer(session, transfer_id)

            if transfer is None:
                log.error(
                    "Webhook references missing transfer_id=%s", transfer_id
                )
                return

            if event.event_type == "payment_executed":
                # Advance through any intermediate states to AWAITING_SETTLEMENT
                if transfer.payment_state in (
                    PaymentState.INITIATED,
                    PaymentState.AWAITING_AUTHORIZATION,
                ):
                    await svc.advance_funding_state(
                        transfer_id, new_state=PaymentState.AUTHORIZING
                    )
                    transfer = await repo.get_funding_transfer(session, transfer_id)

                if transfer.payment_state == PaymentState.AUTHORIZING:
                    await svc.advance_funding_state(
                        transfer_id, new_state=PaymentState.AWAITING_SETTLEMENT
                    )

                if transfer.payment_state == PaymentState.AWAITING_SETTLEMENT:
                    await svc.credit_from_funding(transfer_id)
                    await events.publish(
                        FundingPaymentReceived(
                            funding_transfer_id=transfer_id,
                            aggregator_payment_id=event.payment_id,
                        )
                    )

            elif event.event_type == "payment_failed":
                # Advance to a state that can transition to FAILED
                if transfer.payment_state == PaymentState.AWAITING_AUTHORIZATION:
                    await svc.advance_funding_state(
                        transfer_id, new_state=PaymentState.AUTHORIZING
                    )
                if transfer.payment_state in (
                    PaymentState.INITIATING,
                    PaymentState.AUTHORIZING,
                    PaymentState.AWAITING_SETTLEMENT,
                ):
                    await svc.advance_funding_state(
                        transfer_id,
                        new_state=PaymentState.FAILED,
                        failure_reason=event.failure_reason,
                    )
                await events.publish(
                    FundingFailed(
                        funding_transfer_id=transfer_id,
                        reason=event.failure_reason,
                    )
                )

            elif event.event_type == "payment_pending":
                # Bank is processing — advance towards AWAITING_SETTLEMENT
                if transfer.payment_state == PaymentState.AWAITING_AUTHORIZATION:
                    await svc.advance_funding_state(
                        transfer_id, new_state=PaymentState.AUTHORIZING
                    )

            await session.commit()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# process_connect_callback
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.modules.wallet.tasks.process_connect_callback",
    queue="default",
    acks_late=True,
)
def process_connect_callback(payload: dict) -> None:  # type: ignore[type-arg]
    """
    Process a TrueLayer bank connection server-to-server webhook.
    Logs the event; the actual connection completion is handled via the
    GET redirect flow (sponsor app calls POST /funding/banks/complete).
    """
    log.info(
        "Bank connect callback received: type=%s", payload.get("type", "unknown")
    )


# ---------------------------------------------------------------------------
# poll_pending_payments  (beat: every 5 minutes)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.modules.wallet.tasks.poll_pending_payments",
    queue="default",
)
def poll_pending_payments() -> None:
    """
    Safety net: poll AWAITING_SETTLEMENT transfers where no webhook has arrived
    within 5 minutes and ask the aggregator for the current status.
    """
    _run(_async_poll_pending_payments())


async def _async_poll_pending_payments() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.modules.wallet import repository as repo
    from app.modules.wallet.openbanking.adapter import get_adapter

    adapter = get_adapter("open_banking")
    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            pending = await repo.list_pending_settlement_transfers(session)

        for transfer_id, aggregator_payment_id in pending:
            try:
                status_result = await adapter.check_status(aggregator_payment_id)
                # Re-use the payment webhook pipeline
                synthetic_payload = {
                    "type": f"payment_{status_result.status}",
                    "payment": {
                        "id": aggregator_payment_id,
                        "status": status_result.status,
                        "failure_reason": status_result.failure_reason,
                    },
                }
                celery_app.send_task(
                    "app.modules.wallet.tasks.process_payment_webhook",
                    args=[synthetic_payload, "open_banking"],
                    queue="critical",
                )
            except Exception:
                log.exception(
                    "Poller: failed to check status for aggregator_payment_id=%s "
                    "(transfer_id=%s)",
                    aggregator_payment_id,
                    transfer_id,
                )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# expire_stale_authorizations  (beat: every 1 minute)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.modules.wallet.tasks.expire_stale_authorizations",
    queue="default",
)
def expire_stale_authorizations() -> None:
    """
    Expire funding transfers that have been in AWAITING_AUTHORIZATION for
    more than 15 minutes (sponsor never completed bank auth).
    """
    _run(_async_expire_stale_authorizations())


async def _async_expire_stale_authorizations() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.core import events
    from app.modules.wallet import repository as repo
    from app.modules.wallet.events import FundingAuthorizationExpired
    from app.modules.wallet.models import PaymentState
    from app.modules.wallet.service import WalletService

    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            stale_ids = await repo.list_stale_authorization_transfer_ids(session)

        for transfer_id in stale_ids:
            try:
                async with factory() as session:
                    svc = WalletService(session)
                    await svc.advance_funding_state(
                        transfer_id, new_state=PaymentState.EXPIRED
                    )
                    await events.publish(
                        FundingAuthorizationExpired(
                            funding_transfer_id=transfer_id
                        )
                    )
                    await session.commit()
                    log.info("Expired stale transfer %s", transfer_id)
            except Exception:
                log.exception("Failed to expire transfer %s", transfer_id)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# warn_expiring_bank_consent  (beat: daily at 08:00 UTC)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.modules.wallet.tasks.warn_expiring_bank_consent",
    queue="bulk",
)
def warn_expiring_bank_consent() -> None:
    """
    Daily: find bank connections whose consent expires within 7 days and
    publish BankConsentExpiring events so the notification module can alert
    sponsors to re-authorise.
    """
    _run(_async_warn_expiring_bank_consent())


async def _async_warn_expiring_bank_consent() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.core import events
    from app.modules.wallet import repository as repo
    from app.modules.wallet.events import BankConsentExpiring

    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as session:
            expiring = await repo.list_expiring_connections(session, days_ahead=7)

        for conn in expiring:
            await events.publish(
                BankConsentExpiring(
                    connection_id=conn.id,
                    sponsor_id=conn.sponsor_id,
                    provider_display_name=conn.provider_display_name,
                    consent_expires_at=(
                        conn.consent_expires_at.isoformat()
                        if conn.consent_expires_at
                        else ""
                    ),
                )
            )
            log.info(
                "BankConsentExpiring published for connection_id=%s sponsor_id=%s",
                conn.id,
                conn.sponsor_id,
            )
    finally:
        await engine.dispose()
