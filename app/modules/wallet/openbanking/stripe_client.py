"""
StripeClient — card payment fallback implementation of PaymentAdapter.

Used when:
  - The sponsor's country is outside UK/EU (no TrueLayer coverage), OR
  - The sponsor explicitly selects card payment.

AIS methods (bank connections) are not applicable to Stripe and raise
NotImplementedError — only TrueLayerClient supports AIS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import httpx

from app.core.exceptions import AggregatorError
from app.modules.wallet.openbanking.adapter import (
    BankAccountInfo,
    InitiationResult,
    PaymentAdapter,
    PaymentStatusResult,
    WebhookEvent,
)

log = logging.getLogger(__name__)

_STRIPE_API_BASE = "https://api.stripe.com"

# Stripe event type → normalised internal event type
_STRIPE_EVENT_MAP: dict[str, str] = {
    "payment_intent.succeeded": "payment_executed",
    "payment_intent.payment_failed": "payment_failed",
    "payment_intent.canceled": "payment_failed",
    "payment_intent.processing": "payment_pending",
    "payment_intent.requires_action": "payment_pending",
}


class StripeClient(PaymentAdapter):
    """
    Stripe PaymentIntent adapter.

    Initiation creates a PaymentIntent and returns the client_secret
    as the "auth_link" — the frontend passes this to Stripe.js to
    complete the card payment.
    """

    def __init__(self) -> None:
        from app.config import settings

        self._secret_key = settings.stripe_secret_key
        self._webhook_secret = settings.stripe_webhook_secret

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._secret_key}",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        async with httpx.AsyncClient(
            base_url=_STRIPE_API_BASE, timeout=15
        ) as http:
            resp = await http.request(
                method,
                path,
                headers=headers,
                data=data,      # Stripe uses form-encoded bodies
            )
        return resp

    # ------------------------------------------------------------------
    # PIS
    # ------------------------------------------------------------------

    async def initiate(
        self,
        *,
        amount: int,
        currency: str,
        beneficiary_name: str,
        idempotency_key: str,
        redirect_uri: str,
        bank_account_id: str | None = None,
    ) -> InitiationResult:
        resp = await self._request(
            "POST",
            "/v1/payment_intents",
            data={
                "amount": str(amount),
                "currency": currency.lower(),
                "payment_method_types[]": "card",
                "description": f"U-FirstSupport wallet funding — {beneficiary_name}",
                "metadata[idempotency_key]": idempotency_key,
            },
            idempotency_key=idempotency_key,
        )
        if resp.status_code not in (200, 201):
            raise AggregatorError(
                "Stripe PaymentIntent creation failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )
        data = resp.json()
        # The client_secret is what the frontend uses with Stripe.js to confirm.
        # We return it as auth_link so the route layer can pass it to the client.
        return InitiationResult(
            payment_id=data["id"],
            auth_link=data["client_secret"],
        )

    async def check_status(self, payment_id: str) -> PaymentStatusResult:
        resp = await self._request("GET", f"/v1/payment_intents/{payment_id}")
        if resp.status_code == 404:
            raise AggregatorError(
                f"Stripe PaymentIntent {payment_id} not found.",
                details={"payment_id": payment_id},
            )
        if resp.status_code != 200:
            raise AggregatorError(
                "Stripe get PaymentIntent failed.",
                details={"status": resp.status_code},
            )
        data = resp.json()
        stripe_status = data.get("status", "")
        normalised = {
            "succeeded": "executed",
            "processing": "pending",
            "requires_action": "pending",
            "requires_confirmation": "pending",
            "canceled": "failed",
            "requires_payment_method": "failed",
        }.get(stripe_status, "pending")

        last_error = data.get("last_payment_error")
        failure_reason = last_error.get("message") if last_error else None

        return PaymentStatusResult(
            payment_id=payment_id,
            status=normalised,
            failure_reason=failure_reason,
        )

    async def verify_webhook(
        self, body: bytes, headers: dict[str, str]
    ) -> None:
        """
        Verify Stripe webhook signature.

        Stripe-Signature header format:
            t=TIMESTAMP,v1=HMAC_SHA256_HEX,...
        Signed payload: TIMESTAMP + "." + raw_body
        """
        sig_header = headers.get("stripe-signature") or headers.get(
            "Stripe-Signature"
        )
        if not sig_header:
            raise AggregatorError(
                "Missing Stripe-Signature header.",
                details={"header": "Stripe-Signature"},
            )

        # Parse t= and v1= from the header
        parts = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in sig_header.split(",") if "=" in p}
        timestamp = parts.get("t", "")
        v1_sig = parts.get("v1", "")

        if not timestamp or not v1_sig:
            raise AggregatorError("Malformed Stripe-Signature header.")

        # Replay prevention: reject webhooks older than 5 minutes
        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > 300:
                raise AggregatorError(
                    "Stripe webhook timestamp is too old (replay prevention).",
                    details={"timestamp": timestamp},
                )
        except ValueError:
            raise AggregatorError("Invalid timestamp in Stripe-Signature header.")

        signed_payload = f"{timestamp}.".encode() + body
        expected = hmac.new(
            self._webhook_secret.encode(), signed_payload, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, v1_sig):
            raise AggregatorError("Invalid Stripe webhook signature.")

    async def parse_webhook(self, body: bytes) -> WebhookEvent:
        data = json.loads(body)
        event_type_raw: str = data.get("type", "")
        normalised = _STRIPE_EVENT_MAP.get(event_type_raw, "payment_pending")

        # PaymentIntent object is under data.object
        pi = data.get("data", {}).get("object", {})
        payment_id: str = pi.get("id", "")
        bank_status: str = pi.get("status", event_type_raw)
        last_error = pi.get("last_payment_error")
        failure_reason: str | None = last_error.get("message") if last_error else None

        return WebhookEvent(
            event_type=normalised,
            payment_id=payment_id,
            bank_status=bank_status,
            failure_reason=failure_reason,
        )

    async def refund(
        self, payment_id: str, *, amount: int, idempotency_key: str
    ) -> str:
        resp = await self._request(
            "POST",
            "/v1/refunds",
            data={
                "payment_intent": payment_id,
                "amount": str(amount),
            },
            idempotency_key=idempotency_key,
        )
        if resp.status_code not in (200, 201):
            raise AggregatorError(
                "Stripe refund failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )
        return resp.json()["id"]

    # ------------------------------------------------------------------
    # AIS — not supported by Stripe
    # ------------------------------------------------------------------

    async def create_connection_session(self, *, redirect_uri: str) -> str:
        raise NotImplementedError("Bank connection sessions are not supported by Stripe.")

    async def complete_connection(
        self, *, code: str, redirect_uri: str
    ) -> BankAccountInfo:
        raise NotImplementedError("Bank connections are not supported by Stripe.")

    async def revoke_consent(self, consent_id: str) -> None:
        raise NotImplementedError("Consent revocation is not supported by Stripe.")
