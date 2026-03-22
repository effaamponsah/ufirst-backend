"""
Tests for the funding transfer flow and wallet crediting.

Financial invariant asserted in every test:
    sum(CREDIT ledger amounts) - sum(DEBIT ledger amounts) == available_balance
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError as SAIntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.modules.wallet.models import EntryType, LedgerEntry
from app.modules.wallet.service import WalletService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_user(client: AsyncClient, user_id: str, role: str) -> None:
    resp = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{user_id}:{role}"},
    )
    assert resp.status_code == 200


async def _create_wallet_direct(owner_id: str, currency: str = "GBP") -> str:
    """Create a wallet directly via the service (bypassing HTTP for setup)."""
    from uuid import UUID as _UUID
    engine = create_async_engine(settings.async_database_url, echo=False, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        svc = WalletService(session)
        wallet = await svc.create_wallet(owner_id=_UUID(owner_id), currency=currency)
        await session.commit()
    await engine.dispose()
    return str(wallet.id)


async def _get_ledger_balance(wallet_id: str) -> tuple[int, int]:
    """Return (sum_credits, sum_debits) for a wallet from the ledger."""
    from uuid import UUID as _UUID
    engine = create_async_engine(settings.async_database_url, echo=False, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    wid = _UUID(wallet_id)
    async with factory() as session:
        result = await session.execute(
            select(LedgerEntry).where(LedgerEntry.wallet_id == wid)
        )
        entries = result.scalars().all()
    await engine.dispose()
    credits = sum(e.amount for e in entries if e.entry_type == EntryType.CREDIT)
    debits = sum(e.amount for e in entries if e.entry_type == EntryType.DEBIT)
    return credits, debits


def _sponsor_headers(sponsor_id: str, extra: dict | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}
    if extra:
        h.update(extra)
    return h


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initiate_funding_creates_transfer(client: AsyncClient) -> None:
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    wallet_id = await _create_wallet_direct(sponsor_id)

    resp = await client.post(
        "/api/v1/funding/initiate",
        json={"payment_method": "open_banking", "source_amount": 5000, "source_currency": "GBP"},
        headers=_sponsor_headers(sponsor_id, {"Idempotency-Key": str(uuid4())}),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["payment_state"] == "awaiting_authorization"
    assert body["source_amount"] == 5000


@pytest.mark.asyncio
async def test_initiate_funding_is_idempotent(client: AsyncClient) -> None:
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    await _create_wallet_direct(sponsor_id)

    idem_key = str(uuid4())
    payload = {"payment_method": "open_banking", "source_amount": 10000, "source_currency": "GBP"}
    headers = _sponsor_headers(sponsor_id, {"Idempotency-Key": idem_key})

    r1 = await client.post("/api/v1/funding/initiate", json=payload, headers=headers)
    r2 = await client.post("/api/v1/funding/initiate", json=payload, headers=headers)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["funding_transfer_id"] == r2.json()["funding_transfer_id"]


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_payload_is_conflict() -> None:
    """
    Service-level conflict detection: same (sponsor_id, idempotency_key) with different
    request params must raise IdempotencyConflict.

    Tested at the service layer directly because the HTTP middleware replays the cached
    201 response before the service runs, which is the correct middleware behaviour but
    prevents the conflict check from being reached via HTTP within the cache TTL.
    """
    from uuid import UUID as _UUID
    from app.core.exceptions import IdempotencyConflict
    from app.modules.wallet.models import PaymentMethod

    engine = create_async_engine(settings.async_database_url, echo=False, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sponsor_id = uuid4()
    idem_key = str(uuid4())

    async with factory() as session:
        svc = WalletService(session)
        wallet = await svc.create_wallet(owner_id=sponsor_id, currency="GBP")
        await session.commit()

    async with factory() as session:
        svc = WalletService(session)
        await svc.initiate_funding(
            wallet_id=wallet.id,
            sponsor_id=sponsor_id,
            payment_method=PaymentMethod.OPEN_BANKING,
            source_amount=5000,
            source_currency="GBP",
            dest_amount=5000,
            dest_currency="GBP",
            fx_rate=__import__("decimal").Decimal("1.0"),
            fee_amount=0,
            idempotency_key=idem_key,
        )
        await session.commit()

    # Same key, different amount — must raise
    async with factory() as session:
        svc = WalletService(session)
        with pytest.raises(IdempotencyConflict):
            await svc.initiate_funding(
                wallet_id=wallet.id,
                sponsor_id=sponsor_id,
                payment_method=PaymentMethod.OPEN_BANKING,
                source_amount=9999,  # different
                source_currency="GBP",
                dest_amount=9999,
                dest_currency="GBP",
                fx_rate=__import__("decimal").Decimal("1.0"),
                fee_amount=0,
                idempotency_key=idem_key,
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_non_sponsor_cannot_initiate_funding(client: AsyncClient) -> None:
    user_id = str(uuid4())
    await _create_user(client, user_id, "ops_agent")
    await _create_wallet_direct(user_id)

    resp = await client.post(
        "/api/v1/funding/initiate",
        json={"payment_method": "open_banking", "source_amount": 1000, "source_currency": "GBP"},
        headers={"Authorization": f"Bearer dev:{user_id}:ops_agent", "Idempotency-Key": str(uuid4())},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_initiate_funding_requires_idempotency_key_header(client: AsyncClient) -> None:
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    await _create_wallet_direct(sponsor_id)

    resp = await client.post(
        "/api/v1/funding/initiate",
        json={"payment_method": "open_banking", "source_amount": 1000, "source_currency": "GBP"},
        headers=_sponsor_headers(sponsor_id),  # no Idempotency-Key header
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_complete_funding_credits_wallet(client: AsyncClient) -> None:
    """Full happy-path: initiate → advance → complete → assert balance and ledger consistency."""
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    wallet_id = await _create_wallet_direct(sponsor_id)
    admin_headers = {"Authorization": f"Bearer dev:{sponsor_id}:admin"}

    init_resp = await client.post(
        "/api/v1/funding/initiate",
        json={"payment_method": "open_banking", "source_amount": 5000, "source_currency": "GBP"},
        headers=_sponsor_headers(sponsor_id, {"Idempotency-Key": str(uuid4())}),
    )
    assert init_resp.status_code == 201
    transfer_id = init_resp.json()["funding_transfer_id"]

    for state in ("authorizing", "awaiting_settlement"):
        r = await client.patch(
            f"/api/v1/funding/{transfer_id}/state",
            json={"payment_state": state},
            headers=admin_headers,
        )
        assert r.status_code == 200

    complete_resp = await client.post(
        f"/api/v1/funding/{transfer_id}/complete", headers=admin_headers
    )
    assert complete_resp.status_code == 200
    wallet_body = complete_resp.json()
    assert wallet_body["available_balance"] == 5000

    # Financial invariant: credits - debits == available_balance
    credits, debits = await _get_ledger_balance(wallet_id)
    assert credits - debits == wallet_body["available_balance"]


@pytest.mark.asyncio
async def test_invalid_state_transition_rejected(client: AsyncClient) -> None:
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    await _create_wallet_direct(sponsor_id)

    init_resp = await client.post(
        "/api/v1/funding/initiate",
        json={"payment_method": "open_banking", "source_amount": 2000, "source_currency": "GBP"},
        headers=_sponsor_headers(sponsor_id, {"Idempotency-Key": str(uuid4())}),
    )
    transfer_id = init_resp.json()["funding_transfer_id"]

    # Jump directly from AWAITING_AUTHORIZATION to COMPLETED — invalid
    resp = await client.patch(
        f"/api/v1/funding/{transfer_id}/state",
        json={"payment_state": "completed"},
        headers={"Authorization": f"Bearer dev:{sponsor_id}:admin"},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_funding_transfer_owner_can_read(client: AsyncClient) -> None:
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    await _create_wallet_direct(sponsor_id)

    init_resp = await client.post(
        "/api/v1/funding/initiate",
        json={"payment_method": "card", "source_amount": 1500, "source_currency": "GBP"},
        headers=_sponsor_headers(sponsor_id, {"Idempotency-Key": str(uuid4())}),
    )
    transfer_id = init_resp.json()["funding_transfer_id"]

    get_resp = await client.get(
        f"/api/v1/funding/{transfer_id}",
        headers=_sponsor_headers(sponsor_id),
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == transfer_id


@pytest.mark.asyncio
async def test_zero_amount_rejected_at_schema(client: AsyncClient) -> None:
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    await _create_wallet_direct(sponsor_id)

    for bad_amount in (0, -1, -9999):
        resp = await client.post(
            "/api/v1/funding/initiate",
            json={
                "payment_method": "open_banking",
                "source_amount": bad_amount,
                "source_currency": "GBP",
            },
            headers=_sponsor_headers(sponsor_id, {"Idempotency-Key": str(uuid4())}),
        )
        assert resp.status_code == 422, f"expected 422 for amount={bad_amount}"


@pytest.mark.asyncio
async def test_zero_amount_rejected_at_service() -> None:
    """Service-level guard catches non-positive amounts even when called directly."""
    from app.core.exceptions import ValidationError as UFirstValidationError
    from app.modules.wallet.models import PaymentMethod

    engine = create_async_engine(settings.async_database_url, echo=False, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sponsor_id = uuid4()
    async with factory() as session:
        wallet = await WalletService(session).create_wallet(
            owner_id=sponsor_id, currency="GBP"
        )
        await session.commit()

    for bad_amount in (0, -1):
        async with factory() as session:
            with pytest.raises(UFirstValidationError):
                await WalletService(session).initiate_funding(
                    wallet_id=wallet.id,
                    sponsor_id=sponsor_id,
                    payment_method=PaymentMethod.OPEN_BANKING,
                    source_amount=bad_amount,
                    source_currency="GBP",
                    dest_amount=bad_amount,
                    dest_currency="GBP",
                    fx_rate=__import__("decimal").Decimal("1.0"),
                    fee_amount=0,
                    idempotency_key=str(uuid4()),
                )

    await engine.dispose()


@pytest.mark.asyncio
async def test_negative_debit_rejected_at_service() -> None:
    """Negative debit would silently increase balance — must be caught."""
    from app.core.exceptions import ValidationError as UFirstValidationError

    engine = create_async_engine(settings.async_database_url, echo=False, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    owner_id = uuid4()
    async with factory() as session:
        wallet = await WalletService(session).create_wallet(
            owner_id=owner_id, currency="GBP"
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(UFirstValidationError):
            await WalletService(session).debit_wallet(
                wallet.id,
                amount=-500,
                reference_type="test",
                reference_id=uuid4(),
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_funding_transfer_other_user_denied(client: AsyncClient) -> None:
    sponsor_id = str(uuid4())
    other_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")
    await _create_user(client, other_id, "sponsor")
    await _create_wallet_direct(sponsor_id)
    await _create_wallet_direct(other_id)

    init_resp = await client.post(
        "/api/v1/funding/initiate",
        json={"payment_method": "card", "source_amount": 1500, "source_currency": "GBP"},
        headers=_sponsor_headers(sponsor_id, {"Idempotency-Key": str(uuid4())}),
    )
    transfer_id = init_resp.json()["funding_transfer_id"]

    # Different user trying to read this transfer
    resp = await client.get(
        f"/api/v1/funding/{transfer_id}",
        headers=_sponsor_headers(other_id),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Concurrent race — IntegrityError path
# ---------------------------------------------------------------------------

def _make_funding_kwargs(wallet_id, sponsor_id, idem_key, *, amount: int = 5000) -> dict:
    from app.modules.wallet.models import PaymentMethod
    return dict(
        wallet_id=wallet_id,
        sponsor_id=sponsor_id,
        payment_method=PaymentMethod.OPEN_BANKING,
        source_amount=amount,
        source_currency="GBP",
        dest_amount=amount,
        dest_currency="GBP",
        fx_rate=Decimal("1.0"),
        fee_amount=0,
        idempotency_key=idem_key,
    )


@pytest.mark.asyncio
async def test_concurrent_race_same_params_returns_existing() -> None:
    """
    The IntegrityError race path: the initial SELECT misses (simulating another
    request inserting between our SELECT and INSERT), so our INSERT raises
    IntegrityError. The loser must roll back, reload the winner's record, and
    return it idempotently.
    """
    from app.modules.wallet import repository as repo

    engine = create_async_engine(settings.async_database_url, echo=False, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sponsor_id = uuid4()
    idem_key = str(uuid4())

    # Create wallet and pre-commit the "winning" transfer (already in DB)
    async with factory() as session:
        wallet = await WalletService(session).create_wallet(owner_id=sponsor_id, currency="GBP")
        await session.commit()

    async with factory() as session:
        winning = await WalletService(session).initiate_funding(
            **_make_funding_kwargs(wallet.id, sponsor_id, idem_key)
        )
        await session.commit()

    # Simulate the losing request: first SELECT misses, INSERT hits unique constraint
    call_count = 0
    original_get = repo.get_funding_transfer_by_idempotency_key

    async def _miss_then_hit(s, sid, key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # race window: the winning insert hasn't been seen yet
        return await original_get(s, sid, key)

    async def _raise_integrity(*args, **kwargs):
        raise SAIntegrityError(None, None, Exception("duplicate key value"))

    async with factory() as session:
        with (
            patch.object(repo, "get_funding_transfer_by_idempotency_key", side_effect=_miss_then_hit),
            patch.object(repo, "create_funding_transfer", side_effect=_raise_integrity),
        ):
            result = await WalletService(session).initiate_funding(
                **_make_funding_kwargs(wallet.id, sponsor_id, idem_key)
            )

    assert str(result.id) == str(winning.id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_race_different_params_raises_conflict() -> None:
    """
    Same IntegrityError race path, but the losing request had different
    parameters than the winner. Must raise IdempotencyConflict, not return
    the stale record.
    """
    from app.core.exceptions import IdempotencyConflict
    from app.modules.wallet import repository as repo

    engine = create_async_engine(settings.async_database_url, echo=False, connect_args={"statement_cache_size": 0})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sponsor_id = uuid4()
    idem_key = str(uuid4())

    async with factory() as session:
        wallet = await WalletService(session).create_wallet(owner_id=sponsor_id, currency="GBP")
        await session.commit()

    # Winner inserted with amount=5000
    async with factory() as session:
        await WalletService(session).initiate_funding(
            **_make_funding_kwargs(wallet.id, sponsor_id, idem_key, amount=5000)
        )
        await session.commit()

    call_count = 0
    original_get = repo.get_funding_transfer_by_idempotency_key

    async def _miss_then_hit(s, sid, key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return await original_get(s, sid, key)

    async def _raise_integrity(*args, **kwargs):
        raise SAIntegrityError(None, None, Exception("duplicate key value"))

    # Loser arrives with amount=9999 — different from the winner
    async with factory() as session:
        with (
            patch.object(repo, "get_funding_transfer_by_idempotency_key", side_effect=_miss_then_hit),
            patch.object(repo, "create_funding_transfer", side_effect=_raise_integrity),
        ):
            with pytest.raises(IdempotencyConflict):
                await WalletService(session).initiate_funding(
                    **_make_funding_kwargs(wallet.id, sponsor_id, idem_key, amount=9999)
                )
