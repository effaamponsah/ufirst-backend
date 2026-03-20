"""
Tests for lazy user provisioning and user retrieval.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_me_creates_skeleton_record(client: AsyncClient) -> None:
    user_id = str(uuid4())
    resp = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{user_id}:sponsor"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user_id
    assert body["role"] == "sponsor"
    assert body["kyc_status"] == "pending"
    assert body["country"] is None
    assert body["beneficiary_relationship"] is None


@pytest.mark.asyncio
async def test_get_me_is_idempotent(client: AsyncClient) -> None:
    user_id = str(uuid4())
    headers = {"Authorization": f"Bearer dev:{user_id}:beneficiary"}

    r1 = await client.get("/api/v1/users/me", headers=headers)
    r2 = await client.get("/api/v1/users/me", headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_complete_profile(client: AsyncClient) -> None:
    user_id = str(uuid4())
    headers = {"Authorization": f"Bearer dev:{user_id}:beneficiary"}

    # Trigger provisioning first
    await client.get("/api/v1/users/me", headers=headers)

    resp = await client.post(
        "/api/v1/onboarding/complete-profile",
        json={
            "country": "GB",
            "phone": "+447700900000",
            "full_name": "Jane Doe",
            "beneficiary_relationship": "spouse",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user_id
    assert body["country"] == "GB"
    assert body["phone"] == "+447700900000"
    assert body["full_name"] == "Jane Doe"
    assert body["beneficiary_relationship"] == "spouse"


@pytest.mark.asyncio
async def test_complete_profile_partial_update(client: AsyncClient) -> None:
    user_id = str(uuid4())
    headers = {"Authorization": f"Bearer dev:{user_id}:sponsor"}

    await client.get("/api/v1/users/me", headers=headers)

    # Only set country
    resp = await client.post(
        "/api/v1/onboarding/complete-profile",
        json={"country": "NG"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["country"] == "NG"
    assert resp.json()["phone"] is None  # unchanged


@pytest.mark.asyncio
async def test_get_me_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_complete_profile_unauthenticated(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/onboarding/complete-profile",
        json={"country": "GB"},
    )
    assert resp.status_code == 401
