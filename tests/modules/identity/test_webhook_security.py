"""
Regression tests for KYC provider webhook signature verification.

These tests patch settings directly to simulate non-dev deployments with and
without secrets configured, without changing the real .env file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KYC_PAYLOAD = {
    "user_id": str(uuid4()),
    "status": "approved",
    "provider_ref": "ref-001",
}


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# KYC webhook — signature verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kyc_webhook_passes_in_dev_mode(client: AsyncClient) -> None:
    """dev_mode=True skips KYC signature check."""
    assert settings.dev_mode is True
    # Webhook will 422 because the user doesn't exist — but NOT 401/500
    resp = await client.post("/api/v1/kyc/webhook", json=_KYC_PAYLOAD)
    assert resp.status_code not in (401, 500)


@pytest.mark.asyncio
async def test_kyc_webhook_fails_closed_when_secret_missing(
    client: AsyncClient,
) -> None:
    """Non-dev + no KYC secret → 500."""
    original_dev = settings.dev_mode
    original_secret = settings.kyc_webhook_secret
    try:
        settings.dev_mode = False
        settings.kyc_webhook_secret = ""
        resp = await client.post("/api/v1/kyc/webhook", json=_KYC_PAYLOAD)
        assert resp.status_code == 500
    finally:
        settings.dev_mode = original_dev
        settings.kyc_webhook_secret = original_secret


@pytest.mark.asyncio
async def test_kyc_webhook_rejects_missing_signature_header(
    client: AsyncClient,
) -> None:
    """Non-dev + secret set + no header → 401."""
    original_dev = settings.dev_mode
    original_secret = settings.kyc_webhook_secret
    try:
        settings.dev_mode = False
        settings.kyc_webhook_secret = "kyc-secret"
        resp = await client.post("/api/v1/kyc/webhook", json=_KYC_PAYLOAD)
        assert resp.status_code == 401
    finally:
        settings.dev_mode = original_dev
        settings.kyc_webhook_secret = original_secret


@pytest.mark.asyncio
async def test_kyc_webhook_rejects_invalid_signature(client: AsyncClient) -> None:
    """Non-dev + secret set + wrong signature → 401."""
    original_dev = settings.dev_mode
    original_secret = settings.kyc_webhook_secret
    try:
        settings.dev_mode = False
        settings.kyc_webhook_secret = "kyc-secret"
        resp = await client.post(
            "/api/v1/kyc/webhook",
            json=_KYC_PAYLOAD,
            headers={"x-kyc-signature": "sha256=deadbeef"},
        )
        assert resp.status_code == 401
    finally:
        settings.dev_mode = original_dev
        settings.kyc_webhook_secret = original_secret


@pytest.mark.asyncio
async def test_kyc_webhook_accepts_valid_signature(client: AsyncClient) -> None:
    """Non-dev + secret set + correct HMAC → request processed (may 404 on unknown user)."""
    secret = "kyc-secret"
    payload_bytes = json.dumps(_KYC_PAYLOAD).encode()
    original_dev = settings.dev_mode
    original_secret = settings.kyc_webhook_secret
    try:
        settings.dev_mode = False
        settings.kyc_webhook_secret = secret
        resp = await client.post(
            "/api/v1/kyc/webhook",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "x-kyc-signature": _sign(payload_bytes, secret),
            },
        )
        # Signature accepted — may 404 (unknown user) but NOT 401/500
        assert resp.status_code not in (401, 500)
    finally:
        settings.dev_mode = original_dev
        settings.kyc_webhook_secret = original_secret
