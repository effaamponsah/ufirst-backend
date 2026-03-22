"""
YapilyClient — implementation of PaymentAdapter for Yapily v2.

Handles:
  - Basic Auth (application_id:application_secret) — no token exchange needed
  - PIS: payment auth request creation, status polling, refunds
  - AIS: account auth request creation, accounts fetch, consent revocation
  - Webhook signature verification (HMAC-SHA256 over raw body)
  - Retries with exponential backoff on 5xx / transport errors

Yapily PIS flow:
  1. POST /payment-auth-requests  →  (payment_request_id, authorisationUrl)
  2. Sponsor authorises at authorisationUrl → redirected back to callback
  3. Yapily sends APPLICATION.PAYMENT.COMPLETED webhook
  4. GET /payment-requests/{id} to poll status

Yapily AIS flow:
  1. POST /account-auth-requests  →  (consent_id, authorisationUrl)
  2. Sponsor authorises → redirected back with consentToken in query param
  3. GET /accounts (Consent: {consentToken}) to fetch account info
  4. DELETE /consents/{consentId} to revoke
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from base64 import b64encode

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

log = logging.getLogger(__name__)

# Yapily payment request status → normalised internal status
_PAYMENT_STATUS_MAP: dict[str, str] = {
    "awaiting_authorization": "pending",
    "authorised": "pending",
    "completed": "executed",
    "failed": "failed",
    "rejected": "rejected",
    "expired": "failed",
    "cancelled": "failed",
}

# Yapily webhook event type → normalised internal event type
_WEBHOOK_EVENT_MAP: dict[str, str] = {
    "application.payment.completed": "payment_executed",
    "application.payment_request.authorised": "payment_pending",
    "application.payment.failed": "payment_failed",
    "application.payment.rejected": "payment_failed",
    "application.payment.expired": "payment_failed",
    "application.payment.cancelled": "payment_failed",
    "application.consent.created": "payment_pending",
    "application.consent.updated": "payment_pending",
}


class YapilyClient(PaymentAdapter):
    """
    Yapily Payments v2 + Accounts API connector.

    Uses HTTP Basic Auth (application_id:application_secret) for all requests.
    No token caching needed — credentials are sent on every call.
    """

    def __init__(self) -> None:
        from app.config import settings

        self._app_id = settings.yapily_application_id
        self._app_secret = settings.yapily_application_secret
        self._webhook_secret = settings.yapily_webhook_secret
        self._base_url = settings.yapily_base_url.rstrip("/")
        self._payee_name = settings.yapily_payee_name
        self._payee_sort_code = settings.yapily_payee_sort_code
        self._payee_account_number = settings.yapily_payee_account_number
        self._payee_iban = settings.yapily_payee_iban
        log.debug(
            "YapilyClient initialised app_id=%s base_url=%s",
            self._app_id,
            self._base_url,
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _auth_header(self) -> str:
        credentials = b64encode(
            f"{self._app_id}:{self._app_secret}".encode()
        ).decode()
        return f"Basic {credentials}"

    # ------------------------------------------------------------------
    # HTTP helper with retries
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        extra_headers: dict[str, str] | None = None,
        retries: int = 3,
    ) -> httpx.Response:
        headers: dict[str, str] = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json;charset=UTF-8",
        }
        if extra_headers:
            headers.update(extra_headers)

        log.debug("Yapily → %s %s body=%s", method, path, json_body)

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
                log.debug(
                    "Yapily ← %s %s status=%d body=%s",
                    method,
                    path,
                    resp.status_code,
                    resp.text[:500],
                )
                if resp.status_code < 500:
                    return resp
                last_exc = AggregatorError(
                    f"Yapily {method} {path} → {resp.status_code}",
                    details={"body": resp.text[:300]},
                )
                log.error(
                    "Yapily 5xx on %s %s (attempt %d/%d): %s",
                    method, path, attempt + 1, retries, resp.text[:300],
                )
            except httpx.TransportError as exc:
                last_exc = AggregatorError(f"Yapily transport error: {exc}")
                log.error(
                    "Yapily transport error on %s %s (attempt %d/%d): %s",
                    method, path, attempt + 1, retries, exc,
                )

            if attempt < retries - 1:
                log.warning(
                    "Yapily request failed (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    retries,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

        raise last_exc or AggregatorError("Yapily request failed after retries.")

    # ------------------------------------------------------------------
    # PIS — Payment Initiation
    # ------------------------------------------------------------------

    def _build_payee(self) -> dict:
        """Build payee object from configured merchant account details."""
        identifications: list[dict] = []
        if self._payee_sort_code and self._payee_account_number:
            identifications.append({
                "type": "SORT_CODE",
                "identification": self._payee_sort_code,
            })
            identifications.append({
                "type": "ACCOUNT_NUMBER",
                "identification": self._payee_account_number,
            })
        if self._payee_iban:
            identifications.append({
                "type": "IBAN",
                "identification": self._payee_iban,
            })
        if not identifications:
            raise AggregatorError(
                "Yapily payee account not configured. "
                "Set YAPILY_PAYEE_SORT_CODE + YAPILY_PAYEE_ACCOUNT_NUMBER "
                "or YAPILY_PAYEE_IBAN."
            )
        return {"name": self._payee_name, "accountIdentifications": identifications}

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
        log.info(
            "Yapily PIS initiate amount=%d %s idempotency_key=%s institution=%s",
            amount, currency, idempotency_key, bank_account_id,
        )

        payload: dict = {
            # Use idempotency_key as applicationUserId — unique per payment and
            # lets Yapily deduplicate retried initiation calls.
            "applicationUserId": idempotency_key,
            "paymentRequest": {
                "type": "DOMESTIC_PAYMENT",
                "paymentIdempotencyId": idempotency_key.replace("-", "")[:35],
                "amount": {
                    # Yapily expects decimal amounts, not minor units
                    "amount": amount / 100,
                    "currency": currency.upper(),
                },
                "reference": f"UFirst {beneficiary_name[:18]}",
                "payee": self._build_payee(),
            },
            "callback": redirect_uri,
        }

        if bank_account_id:
            payload["institutionId"] = bank_account_id

        resp = await self._request("POST", "/payment-auth-requests", json_body=payload)
        if resp.status_code not in (200, 201):
            log.error(
                "Yapily payment initiation failed status=%d body=%s",
                resp.status_code, resp.text[:500],
            )
            raise AggregatorError(
                "Yapily payment initiation failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )

        data = resp.json().get("data", {})
        payment_id: str = data["id"]
        auth_link: str = data.get("authorisationUrl", "")
        log.info(
            "Yapily PIS initiate OK payment_id=%s auth_link=%s",
            payment_id, auth_link,
        )
        return InitiationResult(payment_id=payment_id, auth_link=auth_link)

    async def execute_payment(
        self,
        *,
        amount: int,
        currency: str,
        beneficiary_name: str,
        idempotency_key: str,
        consent_token: str,
    ) -> tuple[str, str]:
        """Step 3 of the Yapily PIS flow: POST /payments with the consentToken.

        Must be called after the user authorises at the bank and the callback
        returns the consentToken. The body must exactly match the paymentRequest
        sent in initiate(). Returns (payment_id, status).
        """
        log.info(
            "Yapily execute_payment amount=%d %s idempotency_key=%s",
            amount, currency, idempotency_key,
        )
        payload = {
            "type": "DOMESTIC_PAYMENT",
            "paymentIdempotencyId": idempotency_key.replace("-", "")[:35],
            "amount": {"amount": amount / 100, "currency": currency.upper()},
            "reference": f"UFirst {beneficiary_name[:18]}",
            "payee": self._build_payee(),
        }
        resp = await self._request(
            "POST", "/payments",
            json_body=payload,
            extra_headers={"Consent": consent_token},
        )
        if resp.status_code not in (200, 201):
            log.error(
                "Yapily execute_payment failed status=%d body=%s",
                resp.status_code, resp.text[:500],
            )
            raise AggregatorError(
                "Yapily payment execution failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )
        data = resp.json().get("data", {})
        payment_id: str = data["id"]
        status: str = data.get("status", "PENDING")
        log.info("Yapily execute_payment OK payment_id=%s status=%s", payment_id, status)
        return payment_id, status

    async def check_status(self, payment_id: str) -> PaymentStatusResult:
        log.debug("Yapily check_status payment_id=%s", payment_id)
        resp = await self._request("GET", f"/payment-requests/{payment_id}")
        if resp.status_code == 404:
            log.warning("Yapily payment not found payment_id=%s", payment_id)
            raise AggregatorError(
                f"Payment {payment_id} not found in Yapily.",
                details={"payment_id": payment_id},
            )
        if resp.status_code != 200:
            log.error(
                "Yapily check_status failed payment_id=%s status=%d body=%s",
                payment_id, resp.status_code, resp.text[:300],
            )
            raise AggregatorError(
                "Yapily get payment-request failed.",
                details={"status": resp.status_code},
            )
        data = resp.json().get("data", {})
        raw_status: str = data.get("paymentResponses", [{}])[0].get("status", "") or data.get("status", "")
        normalised = _PAYMENT_STATUS_MAP.get(raw_status.lower(), "pending")
        failure_reason: str | None = data.get("failureReason") or data.get("error")
        log.info(
            "Yapily check_status payment_id=%s raw=%s normalised=%s",
            payment_id, raw_status, normalised,
        )
        return PaymentStatusResult(
            payment_id=payment_id,
            status=normalised,
            failure_reason=failure_reason,
        )

    async def refund(
        self, payment_id: str, *, amount: int, idempotency_key: str
    ) -> str:
        log.info("Yapily refund payment_id=%s amount=%d", payment_id, amount)
        payload = {
            "paymentIdempotencyId": idempotency_key,
            "amount": {
                "amount": amount / 100,
                "currency": "GBP",   # refund in same currency as original
            },
        }
        resp = await self._request(
            "POST",
            f"/payments/{payment_id}/refunds",
            json_body=payload,
        )
        if resp.status_code not in (200, 201):
            log.error(
                "Yapily refund failed payment_id=%s status=%d body=%s",
                payment_id, resp.status_code, resp.text[:300],
            )
            raise AggregatorError(
                "Yapily refund failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )
        refund_id: str = resp.json().get("data", {}).get("id", "")
        log.info("Yapily refund OK payment_id=%s refund_id=%s", payment_id, refund_id)
        return refund_id

    # ------------------------------------------------------------------
    # Webhook verification
    # ------------------------------------------------------------------

    async def verify_webhook(
        self, body: bytes, headers: dict[str, str]
    ) -> None:
        """Verify HMAC-SHA256 signature on Yapily webhook payloads."""
        log.debug("Yapily verify_webhook headers=%s", list(headers.keys()))
        sig_header = headers.get("x-yapily-signature") or headers.get(
            "X-Yapily-Signature"
        )
        if not sig_header:
            log.warning("Yapily webhook missing X-Yapily-Signature header")
            raise AggregatorError(
                "Missing Yapily webhook signature header.",
                details={"header": "X-Yapily-Signature"},
            )

        expected = "sha256=" + hmac.new(
            self._webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, sig_header):
            log.warning("Yapily webhook signature mismatch")
            raise AggregatorError("Invalid Yapily webhook signature.")

        log.debug("Yapily webhook signature verified OK")

    async def parse_webhook(self, body: bytes) -> WebhookEvent:
        data = json.loads(body)
        # Yapily webhook envelope: { eventType, entityId, payload: {...} }
        raw_type: str = data.get("eventType", "").lower()
        payload = data.get("payload", data)
        payment_id: str = (
            data.get("entityId")
            or payload.get("id")
            or payload.get("paymentRequestId", "")
        )
        bank_status: str = payload.get("status", raw_type)
        failure_reason: str | None = payload.get("failureReason") or payload.get("error")
        event_type = _WEBHOOK_EVENT_MAP.get(raw_type, "payment_pending")
        log.info(
            "Yapily webhook parsed raw_type=%s payment_id=%s normalised=%s",
            raw_type, payment_id, event_type,
        )
        return WebhookEvent(
            event_type=event_type,
            payment_id=payment_id,
            bank_status=bank_status,
            failure_reason=failure_reason,
            timestamp=data.get("createdAt"),
        )

    # ------------------------------------------------------------------
    # AIS — Bank connections
    # ------------------------------------------------------------------

    async def create_connection_session(
        self, *, redirect_uri: str, user_id: str, institution_id: str | None = None
    ) -> str:
        """Start AIS bank link flow. Returns the authorisationUrl for the sponsor.

        user_id: the sponsor's UUID — Yapily echoes it back as
        `application-user-id` in the callback redirect so we can link the
        connection to the right sponsor.
        institution_id is required by Yapily — it identifies the bank the sponsor
        wants to connect (e.g. "monzo", "barclays"). Obtain the full list from
        GET /institutions on the Yapily API.
        """
        log.info(
            "Yapily AIS create_connection_session user_id=%s institution_id=%s redirect_uri=%s",
            user_id, institution_id, redirect_uri,
        )

        if not institution_id:
            raise AggregatorError(
                "Yapily requires an institution_id to start a bank connection. "
                "Fetch the list from GET /institutions and pass the chosen id."
            )

        payload = {
            "applicationUserId": user_id,
            "institutionId": institution_id,
            "callback": redirect_uri,
        }
        resp = await self._request("POST", "/account-auth-requests", json_body=payload)
        if resp.status_code not in (200, 201):
            log.error(
                "Yapily AIS session creation failed status=%d body=%s",
                resp.status_code, resp.text[:500],
            )
            raise AggregatorError(
                "Yapily bank connection session creation failed.",
                details={"status": resp.status_code, "body": resp.text[:300]},
            )

        auth_link: str = resp.json().get("data", {}).get("authorisationUrl", "")
        log.info("Yapily AIS session created auth_link=%s", auth_link)
        return auth_link

    async def _get_consent_data(self, consent_id: str) -> dict:
        """Fetch a consent object from Yapily. Returns the `data` dict."""
        log.debug("Yapily fetch consent consent_id=%s", consent_id)
        resp = await self._request("GET", f"/consents/{consent_id}")
        if resp.status_code != 200:
            log.error(
                "Yapily consent fetch failed consent_id=%s status=%d body=%s",
                consent_id, resp.status_code, resp.text[:300],
            )
            raise AggregatorError(
                "Yapily consent fetch failed.",
                details={"status": resp.status_code, "consent_id": consent_id},
            )
        data: dict = resp.json().get("data", {})
        log.debug(
            "Yapily consent consent_id=%s status=%s institution=%s expires=%s",
            consent_id,
            data.get("status"),
            data.get("institutionId"),
            data.get("expiresAt"),
        )
        return data

    async def complete_connection(
        self, *, code: str, redirect_uri: str  # noqa: ARG002  # redirect_uri unused by Yapily
    ) -> BankAccountInfo:
        """Complete the AIS connection using the consent ID from the Yapily callback.

        `code` is the consentid (UUID) from the ?consentid= query param.
        We fetch the full consent from Yapily to get the consentToken, expiry,
        and institution, then use the consentToken to fetch account details.
        """
        log.info("Yapily AIS complete_connection consent_id=%s", code)

        # 1. Fetch consent object — validates the consent belongs to our application
        #    and gives us the token needed for subsequent account calls.
        consent_data = await self._get_consent_data(code)
        consent_token: str = consent_data.get("consentToken", "")
        if not consent_token:
            log.error(
                "Yapily consent has no consentToken consent_id=%s status=%s",
                code, consent_data.get("status"),
            )
            raise AggregatorError(
                "Yapily consent has no consentToken — it may not be AUTHORIZED yet.",
                details={"consent_id": code, "status": consent_data.get("status")},
            )

        institution_id: str = consent_data.get("institutionId", "")
        consent_expires_at: str = consent_data.get("expiresAt", "")
        log.debug(
            "Yapily consent OK institution_id=%s expires=%s",
            institution_id, consent_expires_at,
        )

        # 2. Fetch account details using the consent token
        log.debug("Yapily fetching accounts with consent token")
        accounts_resp = await self._request(
            "GET", "/accounts", extra_headers={"Consent": consent_token}
        )
        if accounts_resp.status_code != 200:
            log.error(
                "Yapily accounts fetch failed status=%d body=%s",
                accounts_resp.status_code, accounts_resp.text[:300],
            )
            raise AggregatorError(
                "Yapily accounts fetch failed.",
                details={"status": accounts_resp.status_code},
            )

        accounts: list[dict] = accounts_resp.json().get("data", [])
        log.info("Yapily accounts fetched count=%d", len(accounts))
        if not accounts:
            raise AggregatorError("No bank accounts returned by Yapily.")

        acc = accounts[0]
        log.debug("Yapily using first account id=%s currency=%s", acc.get("id"), acc.get("currency"))

        # Prefer IBAN, fall back to SORT_CODE + ACCOUNT_NUMBER
        identifier = ""
        for ident in acc.get("accountIdentifications", []):
            if ident.get("type") == "IBAN":
                identifier = ident["identification"]
                break
        if not identifier:
            sort_code = next(
                (i["identification"] for i in acc.get("accountIdentifications", [])
                 if i.get("type") == "SORT_CODE"),
                "",
            )
            account_number = next(
                (i["identification"] for i in acc.get("accountIdentifications", [])
                 if i.get("type") == "ACCOUNT_NUMBER"),
                "",
            )
            identifier = f"{sort_code}/{account_number}" if sort_code else account_number

        account_names: list[dict] = acc.get("accountNames", [])
        holder_name: str = account_names[0].get("name", "") if account_names else ""

        log.info(
            "Yapily AIS complete_connection OK institution=%s holder=%s identifier=%s",
            institution_id, holder_name, identifier[:8] + "***" if identifier else "",
        )

        return BankAccountInfo(
            external_account_id=acc["id"],
            account_identifier=identifier,
            account_holder_name=holder_name,
            provider_id=institution_id,
            provider_display_name=institution_id,
            currency=acc.get("currency", "GBP"),
            consent_id=code,          # Yapily consent UUID — used to revoke later
            consent_expires_at=consent_expires_at,
        )

    async def revoke_consent(self, consent_id: str) -> None:
        log.info("Yapily revoke_consent consent_id=%s", consent_id)
        resp = await self._request("DELETE", f"/consents/{consent_id}")
        if resp.status_code not in (200, 204):
            log.warning(
                "Yapily consent revocation returned %d for consent_id=%s",
                resp.status_code,
                consent_id,
            )
        else:
            log.info("Yapily consent revoked OK consent_id=%s", consent_id)

    async def get_institutions(self) -> list[Institution]:
        log.debug("Yapily fetching institutions")
        resp = await self._request("GET", "/institutions")
        if resp.status_code != 200:
            log.error(
                "Yapily institutions fetch failed status=%d body=%s",
                resp.status_code, resp.text[:300],
            )
            raise AggregatorError(
                "Yapily institutions fetch failed.",
                details={"status": resp.status_code},
            )

        _PIS_FEATURES = {
            "INITIATE_DOMESTIC_SINGLE_PAYMENT",
            "INITIATE_DOMESTIC_SINGLE_INSTANT_PAYMENT",
        }
        _AIS_FEATURES = {"ACCOUNT_REQUEST"}

        results: list[Institution] = []
        for item in resp.json().get("data", []):
            features: set[str] = set(item.get("features", []))
            logo_url: str | None = next(
                (
                    m["source"]
                    for m in item.get("media", [])
                    if m.get("type") == "icon"
                ),
                None,
            )
            results.append(
                Institution(
                    id=item["id"],
                    name=item.get("name", item["id"]),
                    countries=[
                        c.get("countryCode2", "") for c in item.get("countries", [])
                    ],
                    logo_url=logo_url,
                    supports_payments=bool(features & _PIS_FEATURES),
                    supports_account_info=bool(features & _AIS_FEATURES),
                )
            )
        log.info("Yapily institutions fetched count=%d", len(results))
        return results
