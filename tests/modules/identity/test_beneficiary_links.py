"""
Tests for sponsor ↔ beneficiary link management.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient


async def _create_user(client: AsyncClient, role: str) -> str:
    user_id = str(uuid4())
    resp = await client.post(
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
    assert resp.status_code == 201, resp.text
    return user_id


@pytest.mark.asyncio
async def test_sponsor_links_beneficiary(client: AsyncClient) -> None:
    sponsor_id = await _create_user(client, "sponsor")
    beneficiary_id = await _create_user(client, "beneficiary")

    resp = await client.post(
        f"/api/v1/users/me/beneficiaries/{beneficiary_id}",
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["sponsor_id"] == sponsor_id
    assert body["beneficiary_id"] == beneficiary_id
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_linking_is_idempotent(client: AsyncClient) -> None:
    sponsor_id = await _create_user(client, "sponsor")
    beneficiary_id = await _create_user(client, "beneficiary")
    headers = {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}

    r1 = await client.post(f"/api/v1/users/me/beneficiaries/{beneficiary_id}", headers=headers)
    r2 = await client.post(f"/api/v1/users/me/beneficiaries/{beneficiary_id}", headers=headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_list_beneficiaries(client: AsyncClient) -> None:
    sponsor_id = await _create_user(client, "sponsor")
    b1 = await _create_user(client, "beneficiary")
    b2 = await _create_user(client, "beneficiary")
    headers = {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}

    await client.post(f"/api/v1/users/me/beneficiaries/{b1}", headers=headers)
    await client.post(f"/api/v1/users/me/beneficiaries/{b2}", headers=headers)

    resp = await client.get("/api/v1/users/me/beneficiaries", headers=headers)
    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()}
    assert b1 in ids
    assert b2 in ids


@pytest.mark.asyncio
async def test_remove_beneficiary_link(client: AsyncClient) -> None:
    sponsor_id = await _create_user(client, "sponsor")
    beneficiary_id = await _create_user(client, "beneficiary")
    headers = {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}

    await client.post(f"/api/v1/users/me/beneficiaries/{beneficiary_id}", headers=headers)

    resp = await client.delete(
        f"/api/v1/users/me/beneficiaries/{beneficiary_id}", headers=headers
    )
    assert resp.status_code == 204

    # Should no longer appear in the list
    list_resp = await client.get("/api/v1/users/me/beneficiaries", headers=headers)
    ids = {u["id"] for u in list_resp.json()}
    assert beneficiary_id not in ids


@pytest.mark.asyncio
async def test_non_sponsor_cannot_link(client: AsyncClient) -> None:
    beneficiary_id = await _create_user(client, "beneficiary")
    other_beneficiary_id = await _create_user(client, "beneficiary")

    resp = await client.post(
        f"/api/v1/users/me/beneficiaries/{other_beneficiary_id}",
        headers={"Authorization": f"Bearer dev:{beneficiary_id}:beneficiary"},
    )
    assert resp.status_code == 403
