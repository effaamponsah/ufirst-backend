"""
Tests for wallet creation and basic retrieval.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

_BENEFICIARY_DATA = {
    "full_name": "Test Beneficiary",
    "phone": "+2348012345678",
    "country": "NG",
    "beneficiary_relationship": "child",
}


@pytest.mark.asyncio
async def test_create_wallet_on_user_creation(client: AsyncClient) -> None:
    """
    Wallets are created externally (e.g., via admin/ops after KYC).
    This test verifies a newly created beneficiary has no wallet (404).
    Beneficiaries are sponsor-created, so we provision via the sponsor flow.
    """
    sponsor_id = str(uuid4())
    await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    create_resp = await client.post(
        "/api/v1/users/me/beneficiaries",
        json=_BENEFICIARY_DATA,
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert create_resp.status_code == 201
    beneficiary_id = create_resp.json()["id"]

    # No wallet yet — 404 expected
    resp = await client.get(
        "/api/v1/wallets/me",
        headers={"Authorization": f"Bearer dev:{beneficiary_id}:beneficiary"},
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
