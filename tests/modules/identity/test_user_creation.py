"""
Tests for the Supabase user.created webhook and user retrieval.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_webhook_creates_user(client: AsyncClient) -> None:
    user_id = str(uuid4())
    payload = {
        "type": "INSERT",
        "table": "users",
        "schema": "auth",
        "record": {
            "id": user_id,
            "email": "sponsor@example.com",
            "phone": "+447700900000",
            "raw_app_meta_data": {"role": "sponsor"},
            "raw_user_meta_data": {"full_name": "Alice Smith"},
        },
    }

    resp = await client.post("/api/v1/auth/webhook/user-created", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == user_id
    assert body["role"] == "sponsor"
    assert body["kyc_status"] == "pending"
    assert body["full_name"] == "Alice Smith"


@pytest.mark.asyncio
async def test_webhook_is_idempotent(client: AsyncClient) -> None:
    user_id = str(uuid4())
    payload = {
        "type": "INSERT",
        "table": "users",
        "schema": "auth",
        "record": {
            "id": user_id,
            "email": f"{user_id}@example.com",
            "phone": None,
            "raw_app_meta_data": {"role": "beneficiary"},
            "raw_user_meta_data": {},
        },
    }

    r1 = await client.post("/api/v1/auth/webhook/user-created", json=payload)
    r2 = await client.post("/api/v1/auth/webhook/user-created", json=payload)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_webhook_rejects_unknown_role(client: AsyncClient) -> None:
    payload = {
        "type": "INSERT",
        "table": "users",
        "schema": "auth",
        "record": {
            "id": str(uuid4()),
            "email": "unknown@example.com",
            "phone": None,
            "raw_app_meta_data": {"role": "superuser"},
            "raw_user_meta_data": {},
        },
    }
    resp = await client.post("/api/v1/auth/webhook/user-created", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_me(client: AsyncClient) -> None:
    user_id = str(uuid4())

    # Create the user via webhook first
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
                "raw_app_meta_data": {"role": "sponsor"},
                "raw_user_meta_data": {},
            },
        },
    )

    resp = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{user_id}:sponsor"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == user_id


@pytest.mark.asyncio
async def test_get_me_unauthenticated(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401
