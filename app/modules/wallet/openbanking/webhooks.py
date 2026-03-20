"""
Open Banking and Stripe webhook endpoints.

Every inbound webhook follows the same five-step pattern:
  1. Read raw body (before FastAPI/Pydantic consumes it)
  2. Verify aggregator signature — reject with 401 if invalid
  3. Replay prevention — reject if timestamp is older than 5 minutes
  4. Persist raw payload to open_banking_webhooks_log
  5. Dispatch to critical Celery queue — return 200 immediately

The Celery task (app.modules.wallet.tasks) does the actual processing so
that webhook endpoints always respond within the aggregator's timeout window.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AggregatorError
from app.modules.wallet import repository as repo
from app.modules.wallet.openbanking.adapter import get_adapter

log = logging.getLogger(__name__)

webhook_router = APIRouter(tags=["webhooks"])

_REPLAY_WINDOW_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_replay(timestamp_str: str | None, field_name: str = "timestamp") -> None:
    """Raise 400 if the webhook timestamp is older than the replay window."""
    if not timestamp_str:
        return  # no timestamp — skip replay check (not all aggregators include it)
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > _REPLAY_WINDOW_SECONDS:
            raise HTTPException(
                status_code=400,
                detail=f"Webhook {field_name} is too old (replay prevention).",
            )
    except (ValueError, TypeError):
        pass  # unparseable timestamp — skip check, don't reject


async def _log_webhook(
    session: AsyncSession,
    aggregator: str,
    event_type: str,
    payload: dict,  # type: ignore[type-arg]
    signature_valid: bool,
) -> None:
    await repo.create_webhook_log(
        session,
        aggregator=aggregator,
        event_type=event_type,
        payload=payload,
        signature_valid=signature_valid,
    )
    await session.commit()


# ---------------------------------------------------------------------------
# TrueLayer — payment status
# ---------------------------------------------------------------------------


@webhook_router.post("/webhooks/openbanking/payment-status", status_code=200)
async def openbanking_payment_status(request: Request) -> dict:  # type: ignore[type-arg]
    """
    TrueLayer payment status webhook.

    Dispatches to app.modules.wallet.tasks.process_payment_webhook
    on the critical Celery queue.
    """
    raw_body = await request.body()
    adapter = get_adapter("open_banking")

    # 1. Signature verification
    sig_valid = True
    try:
        await adapter.verify_webhook(raw_body, dict(request.headers))
    except AggregatorError as exc:
        log.warning("OB payment webhook signature invalid: %s", exc.message)
        sig_valid = False
        # Log with invalid flag then reject
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError:
            data = {"raw": raw_body.decode(errors="replace")}
        async for session in get_db():
            await _log_webhook(session, "truelayer", "unknown", data, False)
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    data = json.loads(raw_body)

    # 2. Replay prevention
    _check_replay(data.get("timestamp"))

    event_type: str = data.get("type", "unknown")

    # 3. Log raw webhook
    async for session in get_db():
        await _log_webhook(session, "truelayer", event_type, data, sig_valid)

    # 4. Dispatch to Celery critical queue
    from app.jobs.celery_app import celery_app

    celery_app.send_task(
        "app.modules.wallet.tasks.process_payment_webhook",
        args=[data, "open_banking"],
        queue="critical",
    )

    return {"received": True}


# ---------------------------------------------------------------------------
# TrueLayer — bank connect callback
# ---------------------------------------------------------------------------


@webhook_router.post("/webhooks/openbanking/connect-callback", status_code=200)
async def openbanking_connect_callback(request: Request) -> dict:  # type: ignore[type-arg]
    """
    TrueLayer server-to-server webhook sent when a bank connection is established.
    Dispatches to app.modules.wallet.tasks.process_connect_callback on the
    default Celery queue.
    """
    raw_body = await request.body()
    adapter = get_adapter("open_banking")

    try:
        await adapter.verify_webhook(raw_body, dict(request.headers))
    except AggregatorError as exc:
        log.warning("OB connect webhook signature invalid: %s", exc.message)
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    data = json.loads(raw_body)
    _check_replay(data.get("timestamp"))

    async for session in get_db():
        await _log_webhook(
            session, "truelayer", data.get("type", "connect"), data, True
        )

    from app.jobs.celery_app import celery_app

    celery_app.send_task(
        "app.modules.wallet.tasks.process_connect_callback",
        args=[data],
        queue="default",
    )

    return {"received": True}


# ---------------------------------------------------------------------------
# Stripe — payment status (Phase 3.8)
# ---------------------------------------------------------------------------


@webhook_router.post("/webhooks/stripe/payment-status", status_code=200)
async def stripe_payment_status(request: Request) -> dict:  # type: ignore[type-arg]
    """
    Stripe PaymentIntent webhook.
    Stripe-Signature header carries t= + v1= HMAC-SHA256.
    """
    raw_body = await request.body()
    adapter = get_adapter("card")

    try:
        await adapter.verify_webhook(raw_body, dict(request.headers))
    except AggregatorError as exc:
        log.warning("Stripe webhook signature invalid: %s", exc.message)
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    data = json.loads(raw_body)
    event_type: str = data.get("type", "unknown")

    async for session in get_db():
        await _log_webhook(session, "stripe", event_type, data, True)

    from app.jobs.celery_app import celery_app

    celery_app.send_task(
        "app.modules.wallet.tasks.process_payment_webhook",
        args=[data, "card"],
        queue="critical",
    )

    return {"received": True}
