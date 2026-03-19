"""
Shared test fixtures.

All tests use real PostgreSQL and Redis via testcontainers — no mocking at the
infrastructure level. Each test runs inside a transaction that is rolled back
on teardown, so tests are fully isolated without needing to truncate tables.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Generator
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.database import Base, get_db
from app.main import app

# ---------------------------------------------------------------------------
# Container lifecycle — shared across the entire test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer, None, None]:
    with RedisContainer("redis:7-alpine") as r:
        yield r


# ---------------------------------------------------------------------------
# Async engine — session-scoped, created once against the test container
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def db_engine(postgres_container: PostgresContainer):
    url = postgres_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    )
    engine = create_async_engine(url, echo=False)

    async with engine.begin() as conn:
        # Create schemas
        for schema in ["identity", "wallet", "card", "transaction", "vendor", "compliance", "notification", "reporting"]:
            await conn.execute(
                __import__("sqlalchemy").text(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            )
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Per-test transaction rollback — gives each test a clean slate
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_connection(db_engine) -> AsyncGenerator[AsyncConnection, None]:
    async with db_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# HTTP test client with auth injection
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP client wired to the FastAPI app with the real DB session injected.
    Auth uses dev mode tokens: 'dev:<user_id>:<role>'
    """
    app.dependency_overrides[get_db] = lambda: db_session  # type: ignore[assignment]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth header helpers
# ---------------------------------------------------------------------------


def auth_header(user_id: str, role: str) -> dict[str, str]:
    """Generate a dev-mode Authorization header."""
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
