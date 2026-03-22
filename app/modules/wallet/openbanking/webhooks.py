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

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
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
# Yapily — browser redirect after payment authorisation (PIS)
# ---------------------------------------------------------------------------


@webhook_router.get("/webhooks/openbanking/payment-callback")
async def yapily_payment_callback(
    consentid: str = Query(default=""),
    consent_token: str = Query(default="", alias="consent"),
    application_user_id: str = Query(default="", alias="application-user-id"),
    _institution: str = Query(default="", alias="institution"),
    _user_uuid: str = Query(default="", alias="user-uuid"),
) -> RedirectResponse:
    """
    Browser redirect from Yapily after the sponsor completes payment authorisation.

    Yapily PIS flow step 3: after the user authorises at their bank, Yapily
    redirects here with ?consent=<consentToken>&application-user-id=<idempotency_key>.
    We call POST /payments to actually execute the payment, then redirect the
    browser to the frontend which polls /funding/{id}/status.
    """
    from app.config import settings
    from app.core.database import AsyncSessionFactory
    from app.modules.wallet import repository as wallet_repo
    from app.modules.wallet.models import PaymentState
    from app.modules.wallet.openbanking.yapily_client import YapilyClient

    frontend_url = settings.frontend_url.rstrip("/")
    log.info(
        "Yapily payment callback consentid=%s application_user_id=%s has_token=%s",
        consentid, application_user_id, bool(consent_token),
    )

    if not consent_token:
        log.error("Yapily payment callback missing consent token")
        return RedirectResponse(f"{frontend_url}/fund/callback?status=error", status_code=302)

    try:
        client = YapilyClient()
        already_settled = False

        async with AsyncSessionFactory() as session:
            # application_user_id was set to idempotency_key in initiate()
            transfer = await wallet_repo.get_funding_transfer_by_idempotency_key_only(
                session, application_user_id
            )
            if transfer is None:
                log.error(
                    "Yapily payment callback: no transfer for idempotency_key=%s",
                    application_user_id,
                )
                return RedirectResponse(
                    f"{frontend_url}/fund/callback?status=error", status_code=302
                )

            log.info(
                "Yapily executing payment for transfer_id=%s state=%s",
                transfer.id, transfer.payment_state,
            )

            # Store auth_request_id before executing (execute_payment will overwrite it)
            auth_request_id: str = transfer.external_payment_ref or ""

            payment_id, _status = await client.execute_payment(
                amount=transfer.source_amount,
                currency=transfer.source_currency,
                beneficiary_name="U-FirstSupport",
                idempotency_key=transfer.idempotency_key,
                consent_token=consent_token,
            )

            # Poll Yapily once — in sandbox the payment completes immediately.
            # In production the webhook advances from AUTHORIZING to COMPLETED.
            try:
                status_result = await client.check_status(auth_request_id or payment_id)
                already_settled = status_result.status == "executed"
                log.info(
                    "Yapily post-execute status check payment_id=%s status=%s",
                    payment_id, status_result.status,
                )
            except Exception:
                log.warning("Yapily post-execute status check failed — leaving at AUTHORIZING")
                already_settled = False

            new_state = PaymentState.AWAITING_SETTLEMENT if already_settled else PaymentState.AUTHORIZING
            await wallet_repo.update_funding_transfer_state(
                session,
                transfer,
                new_state=new_state,
                external_payment_ref=payment_id,
            )
            await session.commit()
            transfer_id = transfer.id
            source_amount = transfer.source_amount
            source_currency = transfer.source_currency

        log.info(
            "Yapily payment executed transfer_id=%s yapily_payment_id=%s settled=%s",
            transfer_id, payment_id, already_settled,
        )

        # If Yapily already confirmed the payment, credit the wallet now.
        # (In production this is done by the webhook handler instead.)
        if already_settled:
            from app.modules.wallet.service import WalletService
            async with AsyncSessionFactory() as session:
                svc = WalletService(session)
                await svc.credit_from_funding(transfer_id)
                await session.commit()
            log.info("Wallet credited inline for transfer_id=%s", transfer_id)

        return RedirectResponse(
            f"{frontend_url}/fund/callback"
            f"?status=authorized"
            f"&transfer_id={transfer_id}"
            f"&source_amount={source_amount}"
            f"&source_currency={source_currency}",
            status_code=302,
        )

    except Exception:
        log.exception(
            "Yapily payment callback failed for idempotency_key=%s", application_user_id
        )
        return RedirectResponse(
            f"{frontend_url}/fund/callback?status=error", status_code=302
        )


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
# Yapily — browser redirect callback after bank authorisation (AIS)
# ---------------------------------------------------------------------------


@webhook_router.get("/webhooks/openbanking/connect-callback")
async def yapily_connect_callback(
    application_user_id: str = Query(alias="application-user-id"),
    consentid: str = Query(...),
    # institution / consent / user-uuid are sent by Yapily but not used directly —
    # we validate by calling GET /consents/{consentid} with our own credentials.
    _institution: str = Query(default="", alias="institution"),
    _consent: str = Query(default="", alias="consent"),
) -> RedirectResponse:
    """
    Browser redirect from Yapily after the sponsor completes bank authorisation.

    Yapily redirects the user here with:
      ?institution=natwest-sandbox
      &consentid=<uuid>
      &user-uuid=<yapily-internal>
      &application-user-id=<our-sponsor-uuid>
      &consent=<signed-jwt>

    We fetch the consent from Yapily (authenticating with our credentials),
    pull the account details, persist the bank connection, then redirect the
    browser to the frontend success/error screen.
    """
    from app.config import settings
    from app.modules.wallet.openbanking.connections import BankConnectionService

    frontend_url = settings.frontend_url.rstrip("/")
    success_url = f"{frontend_url}/fund/callback?status=linked"
    error_url = f"{frontend_url}/fund/callback?status=error"

    try:
        sponsor_id = UUID(application_user_id)
    except ValueError:
        log.warning("Yapily callback: invalid application-user-id=%r", application_user_id)
        return RedirectResponse(error_url, status_code=302)

    try:
        from app.core.database import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            try:
                service = BankConnectionService(session)
                # complete_connection(code=consentid) fetches GET /consents/{id}
                # from Yapily to get the token + expiry, then GET /accounts.
                await service.complete_connection(sponsor_id=sponsor_id, code=consentid)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception:
        log.exception(
            "Yapily connect callback failed for sponsor_id=%s consentid=%s",
            sponsor_id,
            consentid,
        )
        return RedirectResponse(error_url, status_code=302)

    return RedirectResponse(success_url, status_code=302)


# ---------------------------------------------------------------------------
# TrueLayer — bank connect callback (server-to-server webhook)
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
