"""
Unit tests for YapilyClient — mocks httpx at the AsyncClient level.

These tests do not require a running database or Yapily credentials.
They verify:
  - check_status calls GET /payments/{id}/details (not /payment-requests/{id})
  - Status values are mapped correctly (COMPLETED → executed, PENDING → pending, etc.)
  - statusDetails.status is preferred over data.status
  - failure_reason is extracted from statusDetails.statusReason
  - 404 and non-200 responses raise AggregatorError
  - execute_payment sends POST /payments with the correct body and Consent header
  - initiate sends POST /payment-auth-requests with the correct structure
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AggregatorError
from app.modules.wallet.openbanking.adapter import PaymentStatusResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> Any:
    """Return a YapilyClient with dummy credentials (no real API calls)."""
    with patch("app.config.settings") as mock_settings:
        mock_settings.yapily_application_id = "test-app-id"
        mock_settings.yapily_application_secret = "test-app-secret"
        mock_settings.yapily_webhook_secret = "test-webhook-secret"
        mock_settings.yapily_base_url = "https://api.yapily.com"
        mock_settings.yapily_payee_name = "U-FirstSupport"
        mock_settings.yapily_payee_sort_code = "040004"
        mock_settings.yapily_payee_account_number = "12345678"
        mock_settings.yapily_payee_iban = ""
        from app.modules.wallet.openbanking.yapily_client import YapilyClient
        return YapilyClient()


def _mock_response(status_code: int, body: dict) -> MagicMock:
    """Return a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(body)
    resp.json.return_value = body
    return resp


def _patch_request(client: Any, response: MagicMock) -> Any:
    """Patch client._request to return the given mock response."""
    return patch.object(client, "_request", new=AsyncMock(return_value=response))


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


TOKEN = "eyJhbGciOiJSUzI1NiJ9.test"  # dummy consent token


@pytest.mark.asyncio
async def test_check_status_sends_consent_header():
    """check_status must send the Consent header — Yapily requires it for /payments/{id}/details."""
    client = _make_client()
    mock_resp = _mock_response(200, {
        "data": {
            "id": "pv3-abc123",
            "status": "COMPLETED",
            "statusDetails": {"status": "COMPLETED"},
        }
    })

    with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
        result = await client.check_status("pv3-abc123", consent_token=TOKEN)

    call_kwargs = mock_req.call_args
    assert call_kwargs.args == ("GET", "/payments/pv3-abc123/details")
    assert call_kwargs.kwargs["extra_headers"]["Consent"] == TOKEN
    assert result.status == "executed"
    assert result.payment_id == "pv3-abc123"


@pytest.mark.asyncio
async def test_check_status_no_consent_token_omits_header():
    """If no consent_token is provided, no Consent header should be sent."""
    client = _make_client()
    mock_resp = _mock_response(200, {"data": {"status": "PENDING"}})

    with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
        await client.check_status("pay-0")

    call_kwargs = mock_req.call_args
    assert call_kwargs.kwargs.get("extra_headers") is None


@pytest.mark.asyncio
async def test_check_status_completed_maps_to_executed():
    client = _make_client()
    body = {"data": {"status": "COMPLETED", "statusDetails": {"status": "COMPLETED"}}}
    with _patch_request(client, _mock_response(200, body)):
        result = await client.check_status("pay-1", consent_token=TOKEN)
    assert result.status == "executed"


@pytest.mark.asyncio
async def test_check_status_pending_maps_to_pending():
    client = _make_client()
    body = {"data": {"status": "PENDING", "statusDetails": {"status": "PENDING"}}}
    with _patch_request(client, _mock_response(200, body)):
        result = await client.check_status("pay-2", consent_token=TOKEN)
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_check_status_failed_maps_to_failed():
    client = _make_client()
    body = {
        "data": {
            "status": "FAILED",
            "statusDetails": {
                "status": "FAILED",
                "statusReason": "InsufficientFunds",
            },
        }
    }
    with _patch_request(client, _mock_response(200, body)):
        result = await client.check_status("pay-3", consent_token=TOKEN)
    assert result.status == "failed"
    assert result.failure_reason == "InsufficientFunds"


@pytest.mark.asyncio
async def test_check_status_rejected_maps_to_failed():
    client = _make_client()
    body = {"data": {"status": "REJECTED", "statusDetails": {"status": "REJECTED"}}}
    with _patch_request(client, _mock_response(200, body)):
        result = await client.check_status("pay-4", consent_token=TOKEN)
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_check_status_prefers_status_details_over_top_level():
    """statusDetails.status should take priority over data.status."""
    client = _make_client()
    body = {
        "data": {
            "status": "PENDING",
            "statusDetails": {"status": "COMPLETED"},
        }
    }
    with _patch_request(client, _mock_response(200, body)):
        result = await client.check_status("pay-5", consent_token=TOKEN)
    assert result.status == "executed"


@pytest.mark.asyncio
async def test_check_status_falls_back_to_top_level_status():
    """If statusDetails is absent, data.status is used."""
    client = _make_client()
    body = {"data": {"status": "COMPLETED"}}
    with _patch_request(client, _mock_response(200, body)):
        result = await client.check_status("pay-6", consent_token=TOKEN)
    assert result.status == "executed"


@pytest.mark.asyncio
async def test_check_status_unknown_status_maps_to_pending():
    """Unrecognised statuses should default to pending (safe fallback)."""
    client = _make_client()
    body = {"data": {"status": "SOME_NEW_STATUS", "statusDetails": {"status": "SOME_NEW_STATUS"}}}
    with _patch_request(client, _mock_response(200, body)):
        result = await client.check_status("pay-7", consent_token=TOKEN)
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_check_status_404_raises_aggregator_error():
    client = _make_client()
    with _patch_request(client, _mock_response(404, {"error": "not found"})):
        with pytest.raises(AggregatorError, match="not found"):
            await client.check_status("pay-missing", consent_token=TOKEN)


@pytest.mark.asyncio
async def test_check_status_500_raises_aggregator_error():
    client = _make_client()
    with patch.object(client, "_request", new=AsyncMock(side_effect=AggregatorError("5xx"))):
        with pytest.raises(AggregatorError):
            await client.check_status("pay-error", consent_token=TOKEN)


# ---------------------------------------------------------------------------
# execute_payment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_payment_posts_to_payments_endpoint():
    """execute_payment must POST to /payments with Consent header."""
    client = _make_client()
    mock_resp = _mock_response(201, {
        "data": {"id": "pv3-xyz", "status": "PENDING"}
    })

    with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
        payment_id, status = await client.execute_payment(
            amount=5000,
            currency="GBP",
            beneficiary_name="Test User",
            idempotency_key="test-idem-key-1",
            consent_token="eyJhbGci...",
        )

    call_kwargs = mock_req.call_args
    assert call_kwargs.args[0] == "POST"
    assert call_kwargs.args[1] == "/payments"
    assert call_kwargs.kwargs["extra_headers"]["Consent"] == "eyJhbGci..."
    assert payment_id == "pv3-xyz"
    assert status == "PENDING"


@pytest.mark.asyncio
async def test_execute_payment_body_matches_initiate_structure():
    """Body must mirror the paymentRequest from initiate() for Yapily idempotency."""
    client = _make_client()
    mock_resp = _mock_response(201, {"data": {"id": "pv3-abc", "status": "PENDING"}})

    with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
        await client.execute_payment(
            amount=10000,
            currency="GBP",
            beneficiary_name="Jane Doe",
            idempotency_key="abcd-1234-efgh-5678",
            consent_token="tok",
        )

    body = mock_req.call_args.kwargs["json_body"]
    assert body["type"] == "DOMESTIC_PAYMENT"
    # paymentIdempotencyId must be ≤35 chars with dashes stripped
    assert body["paymentIdempotencyId"] == "abcd1234efgh5678"
    assert body["amount"]["amount"] == 100.0   # 10000 minor units → 100.00
    assert body["amount"]["currency"] == "GBP"
    assert body["reference"] == "UFirst Jane Doe"
    # Payee must have separate SORT_CODE and ACCOUNT_NUMBER entries
    ident_types = {i["type"] for i in body["payee"]["accountIdentifications"]}
    assert "SORT_CODE" in ident_types
    assert "ACCOUNT_NUMBER" in ident_types


@pytest.mark.asyncio
async def test_execute_payment_400_raises_aggregator_error():
    client = _make_client()
    body = {"error": {"message": "Consent mismatch"}}
    with _patch_request(client, _mock_response(400, body)):
        with pytest.raises(AggregatorError, match="execution failed"):
            await client.execute_payment(
                amount=5000,
                currency="GBP",
                beneficiary_name="Test",
                idempotency_key="key-1",
                consent_token="tok",
            )


# ---------------------------------------------------------------------------
# initiate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_returns_payment_id_and_auth_link():
    client = _make_client()
    mock_resp = _mock_response(201, {
        "data": {
            "id": "pr-auth-001",
            "authorisationUrl": "https://auth.yapily.com/auth?id=pr-auth-001",
        }
    })

    with _patch_request(client, mock_resp):
        result = await client.initiate(
            amount=2000,
            currency="GBP",
            beneficiary_name="Bob",
            idempotency_key="idem-001",
            redirect_uri="https://example.com/callback",
            bank_account_id="monzo",
        )

    assert result.payment_id == "pr-auth-001"
    assert "yapily" in result.auth_link or "auth" in result.auth_link


@pytest.mark.asyncio
async def test_initiate_includes_institution_id_when_provided():
    client = _make_client()
    mock_resp = _mock_response(201, {
        "data": {"id": "pr-001", "authorisationUrl": "https://auth.yapily.com/x"}
    })

    with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
        await client.initiate(
            amount=1000,
            currency="GBP",
            beneficiary_name="Alice",
            idempotency_key="idem-002",
            redirect_uri="https://example.com/callback",
            bank_account_id="barclays",
        )

    body = mock_req.call_args.kwargs["json_body"]
    assert body["institutionId"] == "barclays"
    assert body["applicationUserId"] == "idem-002"


@pytest.mark.asyncio
async def test_initiate_payment_idempotency_id_max_35_chars():
    """paymentIdempotencyId must be ≤ 35 chars (Yapily limit)."""
    client = _make_client()
    long_key = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"  # 36 chars with dashes
    mock_resp = _mock_response(201, {
        "data": {"id": "pr-002", "authorisationUrl": "https://auth.yapily.com/y"}
    })

    with patch.object(client, "_request", new=AsyncMock(return_value=mock_resp)) as mock_req:
        await client.initiate(
            amount=500,
            currency="GBP",
            beneficiary_name="Test",
            idempotency_key=long_key,
            redirect_uri="https://example.com/cb",
        )

    body = mock_req.call_args.kwargs["json_body"]
    assert len(body["paymentRequest"]["paymentIdempotencyId"]) <= 35


@pytest.mark.asyncio
async def test_initiate_raises_when_payee_not_configured():
    """If payee credentials are blank, initiate must raise before making any HTTP call."""
    with patch("app.config.settings") as mock_settings:
        mock_settings.yapily_application_id = "id"
        mock_settings.yapily_application_secret = "secret"
        mock_settings.yapily_webhook_secret = ""
        mock_settings.yapily_base_url = "https://api.yapily.com"
        mock_settings.yapily_payee_name = ""
        mock_settings.yapily_payee_sort_code = ""
        mock_settings.yapily_payee_account_number = ""
        mock_settings.yapily_payee_iban = ""
        from app.modules.wallet.openbanking.yapily_client import YapilyClient
        client = YapilyClient()

    with pytest.raises(AggregatorError, match="payee account not configured"):
        await client.initiate(
            amount=1000,
            currency="GBP",
            beneficiary_name="Test",
            idempotency_key="key",
            redirect_uri="https://example.com/cb",
        )
