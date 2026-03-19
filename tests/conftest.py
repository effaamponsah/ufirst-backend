"""
Shared test fixtures.

Tests run against the already-running docker-compose services (PostgreSQL +
Redis). Each test gets a clean database state via table truncation after each
test. The ASGI app manages its own sessions normally — no session sharing
between the test framework and the app (avoids asyncpg event-loop conflicts).

Start the services before running tests:
    docker compose up -d
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.core.database import Base
from app.main import app

# ---------------------------------------------------------------------------
# Engine — session-scoped, reused for the whole test run
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    engine = create_async_engine(settings.async_database_url, echo=False)

    async with engine.begin() as conn:
        for schema in [
            "identity", "wallet", "card", "transaction",
            "vendor", "compliance", "notification", "reporting",
        ]:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Table cleanup — runs after every test to give the next test a clean slate
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_engine) -> AsyncGenerator[None, None]:
    yield
    async with db_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


# ---------------------------------------------------------------------------
# HTTP test client — the app uses its own DB sessions (no override needed)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    FastAPI test client against the real running database.
    Auth uses dev-mode tokens: 'Bearer dev:<user_id>:<role>'
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Auth header helper
# ---------------------------------------------------------------------------


def auth_header(user_id: str, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{user_id}:{role}"}


@pytest.fixture
def sponsor_id() -> str:
    return str(uuid4())


@pytest.fixture
def beneficiary_id() -> str:
    return str(uuid4())


@pytest.fixture
def sponsor_auth(sponsor_id: str) -> dict[str, str]:
    return auth_header(sponsor_id, "sponsor")


@pytest.fixture
def beneficiary_auth(beneficiary_id: str) -> dict[str, str]:
    return auth_header(beneficiary_id, "beneficiary")
