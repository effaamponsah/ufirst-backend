from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.core.middleware import IdempotencyMiddleware


class FakeRedis:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        del ex
        if nx and key in self._values:
            return False
        self._values[key] = value
        return True

    async def delete(self, key: str) -> int:
        existed = key in self._values
        self._values.pop(key, None)
        return int(existed)


@dataclass
class MiddlewareState:
    counters: dict[str, int] = field(
        default_factory=lambda: {"mutate": 0, "one": 0, "two": 0, "slow": 0}
    )
    slow_entered: asyncio.Event = field(default_factory=asyncio.Event)
    slow_release: asyncio.Event = field(default_factory=asyncio.Event)


@pytest_asyncio.fixture(scope="session")
async def db_engine() -> None:
    yield None


@pytest_asyncio.fixture(autouse=True)
async def clean_tables() -> None:
    yield None


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    from app.core import middleware as middleware_module

    fake_redis = FakeRedis()
    monkeypatch.setattr(middleware_module, "get_redis", lambda: fake_redis)

    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)
    state = MiddlewareState()
    app.state.test_state = state

    @app.post("/mutate")
    async def mutate(request: Request) -> JSONResponse:
        payload = await request.json()
        app.state.test_state.counters["mutate"] += 1
        return JSONResponse(
            {
                "call": app.state.test_state.counters["mutate"],
                "payload": payload,
            }
        )

    @app.post("/one")
    async def one() -> JSONResponse:
        app.state.test_state.counters["one"] += 1
        return JSONResponse({"call": app.state.test_state.counters["one"]})

    @app.post("/two")
    async def two() -> JSONResponse:
        app.state.test_state.counters["two"] += 1
        return JSONResponse({"call": app.state.test_state.counters["two"]})

    @app.post("/slow")
    async def slow(request: Request) -> JSONResponse:
        payload = await request.json()
        app.state.test_state.counters["slow"] += 1
        app.state.test_state.slow_entered.set()
        await app.state.test_state.slow_release.wait()
        return JSONResponse(
            {
                "call": app.state.test_state.counters["slow"],
                "payload": payload,
            }
        )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as async_client:
        async_client._test_state = state  # type: ignore[attr-defined]
        yield async_client


def _headers(user_id: str, idempotency_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer dev:{user_id}:sponsor",
        "Idempotency-Key": idempotency_key,
    }


@pytest.mark.asyncio
async def test_same_request_replays_cached_response(client: AsyncClient) -> None:
    user_id = str(uuid4())
    idem_key = str(uuid4())
    headers = _headers(user_id, idem_key)

    first = await client.post("/mutate", json={"amount": 100}, headers=headers)
    second = await client.post("/mutate", json={"amount": 100}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json() == {"call": 1, "payload": {"amount": 100}}
    assert client._test_state.counters["mutate"] == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_same_idempotency_key_is_scoped_per_user(client: AsyncClient) -> None:
    idem_key = str(uuid4())
    first_user_headers = _headers(str(uuid4()), idem_key)
    second_user_headers = _headers(str(uuid4()), idem_key)

    first = await client.post(
        "/mutate",
        json={"amount": 100},
        headers=first_user_headers,
    )
    second = await client.post(
        "/mutate",
        json={"amount": 100},
        headers=second_user_headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["call"] == 1
    assert second.json()["call"] == 2
    assert client._test_state.counters["mutate"] == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_same_idempotency_key_is_scoped_per_route(client: AsyncClient) -> None:
    headers = _headers(str(uuid4()), str(uuid4()))

    first = await client.post("/one", headers=headers)
    second = await client.post("/two", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"call": 1}
    assert second.json() == {"call": 1}
    assert client._test_state.counters["one"] == 1  # type: ignore[attr-defined]
    assert client._test_state.counters["two"] == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_same_key_different_payload_returns_conflict(client: AsyncClient) -> None:
    headers = _headers(str(uuid4()), str(uuid4()))

    first = await client.post("/mutate", json={"amount": 100}, headers=headers)
    second = await client.post("/mutate", json={"amount": 200}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client._test_state.counters["mutate"] == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_concurrent_duplicate_request_is_rejected_while_first_in_progress(
    client: AsyncClient,
) -> None:
    headers = _headers(str(uuid4()), str(uuid4()))

    first_task = asyncio.create_task(
        client.post("/slow", json={"amount": 100}, headers=headers)
    )
    await client._test_state.slow_entered.wait()  # type: ignore[attr-defined]

    second = await client.post("/slow", json={"amount": 100}, headers=headers)
    client._test_state.slow_release.set()  # type: ignore[attr-defined]
    first = await first_task

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DUPLICATE_IDEMPOTENCY_KEY"
    assert client._test_state.counters["slow"] == 1  # type: ignore[attr-defined]
