"""
Tests for sponsor ↔ beneficiary link management.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient

_BENEFICIARY_DATA = {
    "full_name": "Jane Doe",
    "phone": "+2348012345678",
    "country": "NG",
    "beneficiary_relationship": "spouse",
}


async def _create_sponsor(client: AsyncClient) -> str:
    """Provision a sponsor via lazy provisioning."""
    user_id = str(uuid4())
    resp = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{user_id}:sponsor"},
    )
    assert resp.status_code == 200, resp.text
    return user_id


async def _create_beneficiary(client: AsyncClient, sponsor_id: str) -> str:
    """Sponsor creates a beneficiary; returns the new beneficiary's UUID."""
    resp = await client.post(
        "/api/v1/users/me/beneficiaries",
        json=_BENEFICIARY_DATA,
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_sponsor_creates_beneficiary(client: AsyncClient) -> None:
    sponsor_id = await _create_sponsor(client)

    resp = await client.post(
        "/api/v1/users/me/beneficiaries",
        json=_BENEFICIARY_DATA,
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "beneficiary"
    assert body["full_name"] == "Jane Doe"
    assert body["country"] == "NG"
    assert body["beneficiary_relationship"] == "spouse"
    assert body["kyc_status"] == "pending"


@pytest.mark.asyncio
async def test_list_beneficiaries(client: AsyncClient) -> None:
    sponsor_id = await _create_sponsor(client)
    headers = {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}

    b1 = await _create_beneficiary(client, sponsor_id)
    b2 = await _create_beneficiary(client, sponsor_id)

    resp = await client.get("/api/v1/users/me/beneficiaries", headers=headers)
    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()}
    assert b1 in ids
    assert b2 in ids


@pytest.mark.asyncio
async def test_remove_beneficiary_link(client: AsyncClient) -> None:
    sponsor_id = await _create_sponsor(client)
    beneficiary_id = await _create_beneficiary(client, sponsor_id)
    headers = {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}

    resp = await client.delete(
        f"/api/v1/users/me/beneficiaries/{beneficiary_id}", headers=headers
    )
    assert resp.status_code == 204

    list_resp = await client.get("/api/v1/users/me/beneficiaries", headers=headers)
    ids = {u["id"] for u in list_resp.json()}
    assert beneficiary_id not in ids


@pytest.mark.asyncio
async def test_non_sponsor_cannot_create_beneficiary(client: AsyncClient) -> None:
    beneficiary_sponsor_id = str(uuid4())
    await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{beneficiary_sponsor_id}:beneficiary"},
    )
    resp = await client.post(
        "/api/v1/users/me/beneficiaries",
        json=_BENEFICIARY_DATA,
        headers={"Authorization": f"Bearer dev:{beneficiary_sponsor_id}:beneficiary"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_beneficiary_cannot_list_beneficiaries(client: AsyncClient) -> None:
    sponsor_id = await _create_sponsor(client)
    beneficiary_id = await _create_beneficiary(client, sponsor_id)

    resp = await client.get(
        "/api/v1/users/me/beneficiaries",
        headers={"Authorization": f"Bearer dev:{beneficiary_id}:beneficiary"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_beneficiary_cannot_remove_link(client: AsyncClient) -> None:
    sponsor_id = await _create_sponsor(client)
    beneficiary_id = await _create_beneficiary(client, sponsor_id)

    resp = await client.delete(
        f"/api/v1/users/me/beneficiaries/{beneficiary_id}",
        headers={"Authorization": f"Bearer dev:{beneficiary_id}:beneficiary"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sponsor_list_and_remove_happy_path(client: AsyncClient) -> None:
    sponsor_id = await _create_sponsor(client)
    beneficiary_id = await _create_beneficiary(client, sponsor_id)
    headers = {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}

    list_resp = await client.get("/api/v1/users/me/beneficiaries", headers=headers)
    assert list_resp.status_code == 200
    assert any(u["id"] == beneficiary_id for u in list_resp.json())

    del_resp = await client.delete(
        f"/api/v1/users/me/beneficiaries/{beneficiary_id}", headers=headers
    )
    assert del_resp.status_code == 204
