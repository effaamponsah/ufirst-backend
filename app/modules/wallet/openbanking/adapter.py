"""
Abstract payment adapter interface and singleton factory.

All aggregator integrations implement PaymentAdapter.  The factory
get_adapter() returns a singleton so token caches are shared across requests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass
class InitiationResult:
    payment_id: str
    auth_link: str


@dataclass
class PaymentStatusResult:
    payment_id: str
    # Normalised status: "executed" | "pending" | "rejected" | "failed"
    status: str
    failure_reason: str | None = None


@dataclass
class WebhookEvent:
    # Normalised type: "payment_executed" | "payment_failed" | "payment_pending"
    event_type: str
    payment_id: str
    bank_status: str            # Raw status string from the aggregator
    failure_reason: str | None = None
    timestamp: str | None = None


@dataclass
class Institution:
    id: str
    name: str
    countries: list[str]
    logo_url: str | None = None
    # Subset of Yapily feature flags relevant to PIS/AIS
    supports_payments: bool = False
    supports_account_info: bool = False


@dataclass
class BankAccountInfo:
    external_account_id: str
    account_identifier: str     # IBAN or "sort_code/account_number" — will be encrypted
    account_holder_name: str
    provider_id: str
    provider_display_name: str
    currency: str
    consent_id: str
    consent_expires_at: str     # ISO 8601 UTC string


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class PaymentAdapter(ABC):
    """
    Abstract interface for payment aggregator integrations.

    Implementations:
      - TrueLayerClient  (Phase 3   — open banking, default)
      - StripeClient     (Phase 3.8 — card fallback)
    """

    # PIS — Payment Initiation

    @abstractmethod
    async def initiate(
        self,
        *,
        amount: int,
        currency: str,
        beneficiary_name: str,
        idempotency_key: str,
        redirect_uri: str,
        bank_account_id: str | None = None,
    ) -> InitiationResult: ...

    @abstractmethod
    async def check_status(
        self, payment_id: str, *, consent_token: str | None = None
    ) -> PaymentStatusResult: ...

    @abstractmethod
    async def verify_webhook(self, body: bytes, headers: dict[str, str]) -> None:
        """Raise AggregatorError if signature is missing or invalid."""
        ...

    @abstractmethod
    async def parse_webhook(self, body: bytes) -> WebhookEvent: ...

    @abstractmethod
    async def refund(
        self, payment_id: str, *, amount: int, idempotency_key: str
    ) -> str:
        """Returns the refund/reversal ID."""
        ...

    # AIS — Account Information / Bank Connection

    @abstractmethod
    async def create_connection_session(
        self, *, redirect_uri: str, user_id: str, institution_id: str | None = None
    ) -> str:
        """Returns the auth_link for the sponsor to authorise a bank connection.

        user_id: the sponsor's UUID — passed as applicationUserId so the
        aggregator echoes it back in the callback query string.
        institution_id: provider-specific bank identifier (required by Yapily,
        optional / ignored by TrueLayer which has a built-in bank picker).
        """
        ...

    @abstractmethod
    async def complete_connection(
        self, *, code: str, redirect_uri: str
    ) -> BankAccountInfo: ...

    @abstractmethod
    async def revoke_consent(self, consent_id: str) -> None: ...

    # Institution discovery

    @abstractmethod
    async def get_institutions(self) -> list[Institution]:
        """Return the list of banks/institutions the provider supports.

        Returns an empty list for providers that handle institution selection
        themselves (e.g. TrueLayer's built-in bank picker).
        """
        ...


# ---------------------------------------------------------------------------
# Dev / stub adapter (used when credentials are not configured)
# ---------------------------------------------------------------------------


class DevPaymentAdapter(PaymentAdapter):
    """
    No-op adapter used in dev mode or when aggregator credentials are absent.

    Returns a deterministic fake payment_id / auth_link so the full
    FundingTransfer lifecycle can be exercised without hitting external APIs.
    """

    async def initiate(self, *, amount, currency, beneficiary_name, idempotency_key,
                       redirect_uri, bank_account_id=None) -> InitiationResult:
        import hashlib
        fake_id = "dev_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        return InitiationResult(
            payment_id=fake_id,
            auth_link=f"https://dev.example.com/pay/{fake_id}",
        )

    async def check_status(
        self, payment_id: str, *, consent_token: str | None = None
    ) -> PaymentStatusResult:
        return PaymentStatusResult(payment_id=payment_id, status="executed")

    async def verify_webhook(self, body: bytes, headers: dict[str, str]) -> None:
        pass  # accept everything in dev

    async def parse_webhook(self, body: bytes) -> WebhookEvent:
        import json
        data = json.loads(body)
        return WebhookEvent(
            event_type="payment_executed",
            payment_id=data.get("payment_id", "dev_unknown"),
            bank_status="executed",
        )

    async def refund(self, payment_id: str, *, amount: int, idempotency_key: str) -> str:
        return f"dev_refund_{payment_id}"

    async def create_connection_session(
        self, *, redirect_uri: str, user_id: str, institution_id: str | None = None
    ) -> str:
        return "https://dev.example.com/connect"

    async def complete_connection(self, *, code: str, redirect_uri: str) -> BankAccountInfo:
        return BankAccountInfo(
            external_account_id="dev_account",
            account_identifier="GB29NWBK60161331926819",
            account_holder_name="Dev Sponsor",
            provider_id="ob-monzo",
            provider_display_name="Monzo (Dev)",
            currency="GBP",
            consent_id="dev_consent",
            consent_expires_at="",
        )

    async def revoke_consent(self, consent_id: str) -> None:
        pass

    async def get_institutions(self) -> list[Institution]:
        return []


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_adapters: dict[str, PaymentAdapter] = {}


def _is_configured(payment_method: str) -> bool:
    """Return True only when real aggregator credentials are present."""
    from app.config import settings

    if payment_method == "card":
        return bool(settings.stripe_secret_key)
    provider = settings.openbanking_provider.upper()
    if provider == "YAPILY":
        return bool(settings.yapily_application_id and settings.yapily_application_secret)
    return bool(settings.truelayer_client_id and settings.truelayer_client_secret)


def get_adapter(payment_method: str = "open_banking") -> PaymentAdapter:
    """
    Return a shared adapter instance for the given payment method.

    Falls back to DevPaymentAdapter when credentials are absent (dev / test).
    Singletons are used so token caches survive across requests.
    payment_method: "open_banking" | "card"
    """
    from app.config import settings

    # Fall back to dev adapter when credentials are not configured
    if not _is_configured(payment_method):
        key = f"dev_{payment_method}"
        if key not in _adapters:
            _adapters[key] = DevPaymentAdapter()
        return _adapters[key]

    if payment_method == "card":
        if "stripe" not in _adapters:
            from app.modules.wallet.openbanking.stripe_client import StripeClient

            _adapters["stripe"] = StripeClient()
        return _adapters["stripe"]

    provider = settings.openbanking_provider.upper()
    if provider not in _adapters:
        if provider == "TRUELAYER":
            from app.modules.wallet.openbanking.client import TrueLayerClient

            _adapters[provider] = TrueLayerClient()
        elif provider == "YAPILY":
            from app.modules.wallet.openbanking.yapily_client import YapilyClient

            _adapters[provider] = YapilyClient()
        else:
            raise ValueError(f"Unknown open banking provider: {provider}")
    return _adapters[provider]
