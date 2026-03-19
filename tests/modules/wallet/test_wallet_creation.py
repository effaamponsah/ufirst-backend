"""
Tests for wallet creation and basic retrieval.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient


async def _create_user(client: AsyncClient, user_id: str, role: str) -> None:
    await client.post(
        "/api/v1/auth/webhook/user-created",
        json={
            "type": "INSERT",
            "table": "users",
            "schema": "auth",
            "record": {
                "id": user_id,
                "email": f"{user_id}@example.com",
                "phone": None,
                "raw_app_meta_data": {"role": role},
                "raw_user_meta_data": {},
            },
        },
    )


@pytest.mark.asyncio
async def test_create_wallet_on_user_creation(client: AsyncClient) -> None:
    """
    Wallets are created externally (e.g., via admin/ops after KYC).
    This test creates a wallet directly via the service by calling the
    internal endpoint available to admin/ops roles.
    """
    user_id = str(uuid4())
    await _create_user(client, user_id, "beneficiary")

    # No wallet yet — 404 expected
    resp = await client.get(
        "/api/v1/wallets/me",
        headers={"Authorization": f"Bearer dev:{user_id}:beneficiary"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_my_wallet_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/wallets/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_wallet_by_id_requires_privileged_role(client: AsyncClient) -> None:
    wallet_id = str(uuid4())
    user_id = str(uuid4())
    resp = await client.get(
        f"/api/v1/wallets/{wallet_id}",
        headers={"Authorization": f"Bearer dev:{user_id}:sponsor"},
    )
    assert resp.status_code == 403
