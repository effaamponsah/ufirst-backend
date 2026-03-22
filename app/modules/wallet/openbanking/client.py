"""
TrueLayerClient — implementation of PaymentAdapter for TrueLayer v3.

Handles:
  - Client credentials OAuth (token cached on the singleton instance)
  - PIS: payment initiation, status polling, refunds
  - AIS: bank connection sessions, code exchange, consent revocation
  - Webhook signature verification (HMAC-SHA256 over raw body)
  - Retries with exponential backoff on 5xx / transport errors
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.core.exceptions import AggregatorError
from app.modules.wallet.openbanking.adapter import (
    BankAccountInfo,
    InitiationResult,
    Institution,
    PaymentAdapter,
    PaymentStatusResult,
    WebhookEvent,
)
from app.modules.wallet.openbanking.mapper import OpenBankingMapper

log = logging.getLogger(__name__)

_mapper = OpenBankingMapper()


class TrueLayerClient(PaymentAdapter):
    """
    TrueLayer Payments v3 + Data API (AIS) connector.

    Uses client credentials OAuth for M2M auth.
    Token is cached on the singleton instance and refreshed before expiry.
    """

    def __init__(self) -> None:
        from app.config import settings

        self._client_id = settings.truelayer_client_id
        self._client_secret = settings.truelayer_client_secret
        self._webhook_secret = settings.truelayer_webhook_secret
        self._base_url = settings.truelayer_base_url
        self._auth_url = settings.truelayer_auth_url
        self._merchant_account_id = settings.truelayer_merchant_account_id

        # Token cache — refreshed automatically
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if (
            self._token
            and self._token_expires_at
            and now < self._token_expires_at
        ):
            return self._token

        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{self._auth_url}/connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "payments",
                },
            )

        if resp.status_code != 200:
            raise AggregatorError(
                "TrueLayer authentication failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )

        data = resp.json()
        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        # Subtract a 60-second buffer so we refresh before the token actually expires
        self._token_expires_at = now + timedelta(seconds=expires_in - 60)
        return self._token

    # ------------------------------------------------------------------
    # HTTP helper with retries
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        idempotency_key: str | None = None,
        retries: int = 3,
    ) -> httpx.Response:
        token = await self._get_access_token()
        headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        delay = 1.0
        last_exc: Exception | None = None

        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(
                    base_url=self._base_url, timeout=20
                ) as http:
                    resp = await http.request(
                        method, path, headers=headers, json=json_body
                    )
                # Only retry on server errors
                if resp.status_code < 500:
                    return resp
                last_exc = AggregatorError(
                    f"TrueLayer {method} {path} → {resp.status_code}",
                    details={"body": resp.text[:300]},
                )
            except httpx.TransportError as exc:
                last_exc = AggregatorError(f"TrueLayer transport error: {exc}")

            if attempt < retries - 1:
                log.warning(
                    "TrueLayer request failed (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    retries,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

        raise last_exc or AggregatorError("TrueLayer request failed after retries.")

    # ------------------------------------------------------------------
    # PIS — Payment Initiation
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
        provider_selection: dict
        if bank_account_id:
            provider_selection = {
                "type": "preselected",
                "provider_id": bank_account_id,
                "scheme_id": "faster_payments_service",
            }
        else:
            provider_selection = {"type": "user_selected"}

        payload: dict = {
            "amount_in_minor": amount,
            "currency": currency.upper(),
            "payment_method": {
                "type": "bank_transfer",
                "provider_selection": provider_selection,
                "beneficiary": {
                    "type": "merchant_account",
                    "merchant_account_id": self._merchant_account_id,
                    "account_holder_name": beneficiary_name,
                },
            },
            "return_uri": redirect_uri,
        }

        resp = await self._request(
            "POST",
            "/v3/payments",
            json_body=payload,
            idempotency_key=idempotency_key,
        )
        if resp.status_code not in (200, 201):
            raise AggregatorError(
                "TrueLayer payment initiation failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )

        data = resp.json()
        payment_id, auth_link = _mapper.payment_from_initiate_response(data)
        return InitiationResult(payment_id=payment_id, auth_link=auth_link)

    async def check_status(self, payment_id: str) -> PaymentStatusResult:
        resp = await self._request("GET", f"/v3/payments/{payment_id}")
        if resp.status_code == 404:
            raise AggregatorError(
                f"Payment {payment_id} not found in TrueLayer.",
                details={"payment_id": payment_id},
            )
        if resp.status_code != 200:
            raise AggregatorError(
                "TrueLayer get payment failed.",
                details={"status": resp.status_code},
            )
        data = resp.json()
        status, failure = _mapper.status_from_get_payment(data)
        return PaymentStatusResult(
            payment_id=payment_id,
            status=status,
            failure_reason=failure,
        )

    async def refund(
        self, payment_id: str, *, amount: int, idempotency_key: str
    ) -> str:
        resp = await self._request(
            "POST",
            f"/v3/payments/{payment_id}/refunds",
            json_body={"amount_in_minor": amount},
            idempotency_key=idempotency_key,
        )
        if resp.status_code not in (200, 201):
            raise AggregatorError(
                "TrueLayer refund failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )
        return resp.json()["id"]

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    async def verify_webhook(
        self, body: bytes, headers: dict[str, str]
    ) -> None:
        """Verify HMAC-SHA256 signature on TrueLayer webhook payloads."""
        # Try both capitalisation variants
        sig_header = headers.get("x-tl-webhook-signature") or headers.get(
            "X-TL-Webhook-Signature"
        )
        if not sig_header:
            raise AggregatorError(
                "Missing TrueLayer webhook signature header.",
                details={"header": "X-TL-Webhook-Signature"},
            )

        expected = "sha256=" + hmac.new(
            self._webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, sig_header):
            raise AggregatorError("Invalid TrueLayer webhook signature.")

    async def parse_webhook(self, body: bytes) -> WebhookEvent:
        data = json.loads(body)
        event_type, payment_id, bank_status, failure_reason = (
            _mapper.webhook_event_from_payload(data)
        )
        return WebhookEvent(
            event_type=event_type,
            payment_id=payment_id,
            bank_status=bank_status,
            failure_reason=failure_reason,
            timestamp=data.get("timestamp"),
        )

    # ------------------------------------------------------------------
    # AIS — Bank connections
    # ------------------------------------------------------------------

    async def create_connection_session(
        self, *, redirect_uri: str, user_id: str, institution_id: str | None = None
    ) -> str:
        """Start AIS bank link flow. Returns the auth_link for the sponsor."""
        token = await self._get_access_token()
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=15
        ) as http:
            resp = await http.post(
                "/v3/auth-link",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "redirect_uri": redirect_uri,
                    "scopes": ["accounts", "balance"],
                },
            )

        if resp.status_code not in (200, 201):
            raise AggregatorError(
                "TrueLayer bank link session creation failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )
        return resp.json().get("auth_uri", resp.json().get("auth_link", ""))

    async def complete_connection(
        self, *, code: str, redirect_uri: str
    ) -> BankAccountInfo:
        """Exchange authorisation code for AIS access token and fetch account info."""
        async with httpx.AsyncClient(timeout=15) as http:
            token_resp = await http.post(
                f"{self._auth_url}/connect/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )

        if token_resp.status_code != 200:
            raise AggregatorError(
                "TrueLayer authorisation code exchange failed.",
                details={"status": token_resp.status_code},
            )

        token_data = token_resp.json()
        ais_token = token_data["access_token"]

        # consent_expires_at: TrueLayer returns "expires_at" as an ISO timestamp
        consent_expires_at: str = token_data.get("expires_at", "")

        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=15
        ) as http:
            accounts_resp = await http.get(
                "/data/v1/accounts",
                headers={"Authorization": f"Bearer {ais_token}"},
            )

        if accounts_resp.status_code != 200:
            raise AggregatorError(
                "TrueLayer accounts fetch failed.",
                details={"status": accounts_resp.status_code},
            )

        accounts = accounts_resp.json().get("results", [])
        if not accounts:
            raise AggregatorError("No bank accounts returned by TrueLayer.")

        acc = accounts[0]
        account_number = acc.get("account_number", {})
        identifier = account_number.get("iban") or (
            f"{account_number.get('sort_code', '')}"
            f"/{account_number.get('number', '')}"
        )

        provider = acc.get("provider", {})
        return BankAccountInfo(
            external_account_id=acc["account_id"],
            account_identifier=identifier,
            account_holder_name=acc.get("display_name", ""),
            provider_id=provider.get("provider_id", ""),
            provider_display_name=provider.get("display_name", ""),
            currency=acc.get("currency", "GBP"),
            consent_id=acc["account_id"],     # TrueLayer uses account_id as consent ref
            consent_expires_at=consent_expires_at,
        )

    async def revoke_consent(self, consent_id: str) -> None:
        resp = await self._request("DELETE", f"/data/v1/consents/{consent_id}")
        if resp.status_code not in (200, 204):
            log.warning(
                "TrueLayer consent revocation returned %d for consent_id=%s",
                resp.status_code,
                consent_id,
            )

    async def get_institutions(self) -> list[Institution]:
        # TrueLayer embeds its own bank picker in the auth flow — no list needed.
        return []
