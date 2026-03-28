"""
Tests for the TransactionService: authorize, clear, and reverse.

Tests cover the full lifecycle:
  1. Authorization (approved / declined for various reasons)
  2. Clearing (converts hold to permanent debit)
  3. Reversal (releases hold back to available)
  4. Ledger balance consistency
  5. List and dispute endpoints
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.modules.card.models import CardStatus
from app.modules.identity.models import KYCStatus
from app.modules.identity.service import IdentityService
from app.modules.wallet.models import EntryType, LedgerEntry
from app.modules.wallet.service import WalletService


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _create_user(client: AsyncClient, user_id: str, role: str) -> None:
    resp = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{user_id}:{role}"},
    )
    assert resp.status_code == 200, resp.text


def _make_engine():  # type: ignore[return]
    return create_async_engine(
        settings.async_database_url,
        echo=False,
        connect_args={"statement_cache_size": 0},
    )


async def _provision_funded_card(
    client: AsyncClient,
    initial_balance: int = 100_000,
    spending_controls: dict | None = None,  # type: ignore[type-arg]
) -> tuple[str, str, str, str]:
    """
    Create sponsor + beneficiary, fund beneficiary wallet, issue and activate card.

    Returns (sponsor_id, beneficiary_id, wallet_id, card_processor_token).
    """
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")

    # Sponsor creates beneficiary
    resp = await client.post(
        "/api/v1/users/me/beneficiaries",
        json={
            "full_name": "Test Beneficiary",
            "phone": "+2348000000099",
            "country": "NG",
            "beneficiary_relationship": "sibling",
        },
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert resp.status_code == 201, resp.text
    beneficiary_id = resp.json()["id"]

    engine = _make_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Approve KYC
    async with factory() as session:
        identity_svc = IdentityService(session)
        await identity_svc.update_kyc_status(
            UUID(beneficiary_id),
            new_status=KYCStatus.APPROVED,
            provider_ref="test",
        )
        await session.commit()

    # Create wallets for both
    async with factory() as session:
        wallet_svc = WalletService(session)
        await wallet_svc.create_wallet(owner_id=UUID(sponsor_id), currency="GBP")
        beneficiary_wallet = await wallet_svc.create_wallet(
            owner_id=UUID(beneficiary_id), currency="GBP"
        )
        wallet_id = str(beneficiary_wallet.id)
        await session.commit()

    # Fund the beneficiary wallet directly via service
    async with factory() as session:
        wallet_svc = WalletService(session)
        from app.modules.wallet import repository as wallet_repo

        wallet = await wallet_repo.get_wallet(session, UUID(wallet_id))
        assert wallet is not None
        await wallet_repo.credit_wallet(
            session,
            wallet,
            amount=initial_balance,
            reference_type="test_setup",
            reference_id=uuid4(),
            description="Test initial balance",
        )
        await session.commit()

    # Issue card with optional spending controls
    issue_body: dict = {"beneficiary_id": beneficiary_id}  # type: ignore[type-arg]
    if spending_controls:
        issue_body["spending_controls"] = spending_controls
    resp = await client.post(
        "/api/v1/cards/",
        json=issue_body,
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert resp.status_code == 201, resp.text
    card_id = resp.json()["id"]

    # Activate card (simulates UP Nigeria dispatch)
    admin_id = str(uuid4())
    resp = await client.post(
        f"/api/v1/cards/{card_id}/activate",
        headers={"Authorization": f"Bearer dev:{admin_id}:admin"},
    )
    assert resp.status_code == 200, resp.text

    # Get processor token from DB (not exposed in API response — intentional)
    async with factory() as session:
        from app.modules.card import repository as card_repo

        card = await card_repo.get_card(session, UUID(card_id))
        assert card is not None
        processor_token = card.processor_token

    await engine.dispose()
    return sponsor_id, beneficiary_id, wallet_id, processor_token


async def _get_wallet_balances(wallet_id: str) -> tuple[int, int]:
    """Return (available_balance, reserved_balance) for a wallet."""
    engine = _make_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from app.modules.wallet import repository as wallet_repo

        wallet = await wallet_repo.get_wallet(session, UUID(wallet_id))
        assert wallet is not None
        result = wallet.available_balance, wallet.reserved_balance
    await engine.dispose()
    return result


async def _get_ledger_entries(wallet_id: str) -> list[LedgerEntry]:
    """Return all ledger entries for a wallet."""
    engine = _make_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        result = await session.execute(
            select(LedgerEntry).where(LedgerEntry.wallet_id == UUID(wallet_id))
        )
        entries = list(result.scalars().all())
    await engine.dispose()
    return entries


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_approved(client: AsyncClient) -> None:
    """POST authorization with valid card, sufficient balance → APPROVED; balances shift."""
    _, _, wallet_id, processor_token = await _provision_funded_card(client, initial_balance=50_000)

    avail_before, reserved_before = await _get_wallet_balances(wallet_id)
    assert avail_before == 50_000
    assert reserved_before == 0

    resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": f"auth-{uuid4()}",
            "card_token": processor_token,
            "merchant_name": "Test Grocery",
            "amount": 10_000,
            "currency": "GBP",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "APPROVED"
    assert body["authorization_id"] is not None

    avail_after, reserved_after = await _get_wallet_balances(wallet_id)
    assert avail_after == 40_000
    assert reserved_after == 10_000


@pytest.mark.asyncio
async def test_authorize_declined_insufficient_balance(client: AsyncClient) -> None:
    """Balance is 0 → DECLINED with reason INSUFFICIENT_BALANCE; no balance change."""
    _, _, wallet_id, processor_token = await _provision_funded_card(client, initial_balance=0)

    resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": f"auth-{uuid4()}",
            "card_token": processor_token,
            "merchant_name": "Test Shop",
            "amount": 5_000,
            "currency": "GBP",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "DECLINED"
    assert body["reason"] == "INSUFFICIENT_BALANCE"

    avail_after, reserved_after = await _get_wallet_balances(wallet_id)
    assert avail_after == 0
    assert reserved_after == 0


@pytest.mark.asyncio
async def test_authorize_declined_inactive_card(client: AsyncClient) -> None:
    """Frozen card → DECLINED with reason CARD_INACTIVE."""
    sponsor_id, _, wallet_id, processor_token = await _provision_funded_card(
        client, initial_balance=50_000
    )

    # Freeze the card
    # We need the card_id — look it up via the wallet
    engine = _make_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from app.modules.card import repository as card_repo

        card = await card_repo.get_card_for_wallet(session, UUID(wallet_id))
        assert card is not None
        card_id = str(card.id)
    await engine.dispose()

    freeze_resp = await client.post(
        f"/api/v1/cards/{card_id}/freeze",
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert freeze_resp.status_code == 200, freeze_resp.text

    resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": f"auth-{uuid4()}",
            "card_token": processor_token,
            "merchant_name": "Test Shop",
            "amount": 5_000,
            "currency": "GBP",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "DECLINED"
    assert body["reason"] == "CARD_INACTIVE"

    # Balances unchanged
    avail_after, reserved_after = await _get_wallet_balances(wallet_id)
    assert avail_after == 50_000
    assert reserved_after == 0


@pytest.mark.asyncio
async def test_authorize_declined_daily_limit(client: AsyncClient) -> None:
    """daily_limit = 5000, authorize 6000 → DECLINED DAILY_LIMIT_EXCEEDED."""
    _, _, wallet_id, processor_token = await _provision_funded_card(
        client,
        initial_balance=100_000,
        spending_controls={"daily_limit": 5_000},
    )

    resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": f"auth-{uuid4()}",
            "card_token": processor_token,
            "merchant_name": "Test Shop",
            "amount": 6_000,
            "currency": "GBP",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "DECLINED"
    assert body["reason"] == "DAILY_LIMIT_EXCEEDED"

    avail_after, reserved_after = await _get_wallet_balances(wallet_id)
    assert avail_after == 100_000
    assert reserved_after == 0


@pytest.mark.asyncio
async def test_authorize_declined_category(client: AsyncClient) -> None:
    """Category allowlist = ['5411'], MCC = '5812' → DECLINED CATEGORY_NOT_ALLOWED."""
    _, _, wallet_id, processor_token = await _provision_funded_card(
        client,
        initial_balance=100_000,
        spending_controls={"categories": ["5411"]},
    )

    resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": f"auth-{uuid4()}",
            "card_token": processor_token,
            "merchant_name": "Test Restaurant",
            "merchant_category_code": "5812",
            "amount": 3_000,
            "currency": "GBP",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "DECLINED"
    assert body["reason"] == "CATEGORY_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_clearing_after_authorization(client: AsyncClient) -> None:
    """Authorize then clear → auth CLEARED; reserved = 0; DEBIT ledger entry exists."""
    _, _, wallet_id, processor_token = await _provision_funded_card(
        client, initial_balance=50_000
    )

    auth_ref = f"auth-{uuid4()}"
    auth_resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": auth_ref,
            "card_token": processor_token,
            "merchant_name": "Test Grocery",
            "amount": 12_000,
            "currency": "GBP",
        },
    )
    assert auth_resp.status_code == 200, auth_resp.text
    assert auth_resp.json()["decision"] == "APPROVED"

    # available = 38_000, reserved = 12_000

    clear_resp = await client.post(
        "/api/v1/webhooks/card-processor/clearing",
        json={
            "processor_auth_ref": auth_ref,
            "processor_clearing_ref": f"clr-{uuid4()}",
            "cleared_amount": 12_000,
            "cleared_currency": "GBP",
        },
    )
    assert clear_resp.status_code == 200, clear_resp.text
    body = clear_resp.json()
    assert body["cleared_amount"] == 12_000
    assert body["authorization_id"] is not None

    avail_after, reserved_after = await _get_wallet_balances(wallet_id)
    # After clear: available unchanged from post-auth (38_000), reserved back to 0
    assert avail_after == 38_000
    assert reserved_after == 0

    # Check DEBIT ledger entry
    entries = await _get_ledger_entries(wallet_id)
    debit_entries = [e for e in entries if e.entry_type == EntryType.DEBIT]
    assert len(debit_entries) == 1
    assert debit_entries[0].amount == 12_000


@pytest.mark.asyncio
async def test_reversal_after_authorization(client: AsyncClient) -> None:
    """Authorize then reverse → auth REVERSED; reserved = 0; available back to original."""
    _, _, wallet_id, processor_token = await _provision_funded_card(
        client, initial_balance=50_000
    )

    auth_ref = f"auth-{uuid4()}"
    auth_resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": auth_ref,
            "card_token": processor_token,
            "merchant_name": "Test Grocery",
            "amount": 8_000,
            "currency": "GBP",
        },
    )
    assert auth_resp.status_code == 200
    assert auth_resp.json()["decision"] == "APPROVED"

    # available = 42_000, reserved = 8_000

    rev_resp = await client.post(
        "/api/v1/webhooks/card-processor/reversal",
        json={"processor_auth_ref": auth_ref},
    )
    assert rev_resp.status_code == 204, rev_resp.text

    avail_after, reserved_after = await _get_wallet_balances(wallet_id)
    assert avail_after == 50_000  # fully restored
    assert reserved_after == 0


@pytest.mark.asyncio
async def test_ledger_balance_consistency(client: AsyncClient) -> None:
    """
    After a complete authorize→clear cycle:
    sum(CREDIT) - sum(DEBIT) == available_balance + reserved_balance
    """
    _, _, wallet_id, processor_token = await _provision_funded_card(
        client, initial_balance=80_000
    )

    auth_ref = f"auth-{uuid4()}"
    auth_resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": auth_ref,
            "card_token": processor_token,
            "merchant_name": "Test Store",
            "amount": 20_000,
            "currency": "GBP",
        },
    )
    assert auth_resp.json()["decision"] == "APPROVED"

    await client.post(
        "/api/v1/webhooks/card-processor/clearing",
        json={
            "processor_auth_ref": auth_ref,
            "cleared_amount": 20_000,
            "cleared_currency": "GBP",
        },
    )

    entries = await _get_ledger_entries(wallet_id)
    total_credits = sum(e.amount for e in entries if e.entry_type == EntryType.CREDIT)
    total_debits = sum(e.amount for e in entries if e.entry_type == EntryType.DEBIT)

    avail, reserved = await _get_wallet_balances(wallet_id)

    assert total_credits - total_debits == avail + reserved


@pytest.mark.asyncio
async def test_cannot_clear_already_cleared(client: AsyncClient) -> None:
    """Clearing an already-cleared authorization returns a 409."""
    _, _, wallet_id, processor_token = await _provision_funded_card(
        client, initial_balance=50_000
    )

    auth_ref = f"auth-{uuid4()}"
    await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": auth_ref,
            "card_token": processor_token,
            "merchant_name": "Test",
            "amount": 5_000,
            "currency": "GBP",
        },
    )

    clear_payload = {
        "processor_auth_ref": auth_ref,
        "cleared_amount": 5_000,
        "cleared_currency": "GBP",
    }

    r1 = await client.post(
        "/api/v1/webhooks/card-processor/clearing", json=clear_payload
    )
    assert r1.status_code == 200, r1.text

    r2 = await client.post(
        "/api/v1/webhooks/card-processor/clearing", json=clear_payload
    )
    assert r2.status_code in (409, 422), r2.text


@pytest.mark.asyncio
async def test_list_transactions(client: AsyncClient) -> None:
    """GET /transactions/ returns authorized transactions for the user's wallet."""
    sponsor_id, beneficiary_id, wallet_id, processor_token = await _provision_funded_card(
        client, initial_balance=50_000
    )

    # Authorize twice
    for _ in range(2):
        resp = await client.post(
            "/api/v1/webhooks/card-processor/authorization",
            json={
                "processor_auth_ref": f"auth-{uuid4()}",
                "card_token": processor_token,
                "merchant_name": "Test Shop",
                "amount": 1_000,
                "currency": "GBP",
            },
        )
        assert resp.json()["decision"] == "APPROVED"

    list_resp = await client.get(
        "/api/v1/transactions/",
        headers={"Authorization": f"Bearer dev:{beneficiary_id}:beneficiary"},
    )
    assert list_resp.status_code == 200, list_resp.text
    txns = list_resp.json()
    assert len(txns) >= 2
    for txn in txns:
        assert txn["wallet_id"] == wallet_id


@pytest.mark.asyncio
async def test_open_dispute(client: AsyncClient) -> None:
    """POST /transactions/{id}/dispute creates a dispute for the wallet owner."""
    _, beneficiary_id, wallet_id, processor_token = await _provision_funded_card(
        client, initial_balance=50_000
    )

    auth_ref = f"auth-{uuid4()}"
    auth_resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": auth_ref,
            "card_token": processor_token,
            "merchant_name": "Suspicious Store",
            "amount": 15_000,
            "currency": "GBP",
        },
    )
    assert auth_resp.json()["decision"] == "APPROVED"
    authorization_id = auth_resp.json()["authorization_id"]

    dispute_resp = await client.post(
        f"/api/v1/transactions/{authorization_id}/dispute",
        json={"reason": "I did not make this transaction."},
        headers={"Authorization": f"Bearer dev:{beneficiary_id}:beneficiary"},
    )
    assert dispute_resp.status_code == 201, dispute_resp.text
    body = dispute_resp.json()
    assert body["authorization_id"] == authorization_id
    assert body["status"] == "open"
    assert body["reason"] == "I did not make this transaction."


@pytest.mark.asyncio
async def test_dispute_denied_for_wrong_user(client: AsyncClient) -> None:
    """A user who doesn't own the wallet cannot open a dispute."""
    _, _, _, processor_token = await _provision_funded_card(
        client, initial_balance=50_000
    )

    auth_ref = f"auth-{uuid4()}"
    auth_resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": auth_ref,
            "card_token": processor_token,
            "merchant_name": "Test",
            "amount": 5_000,
            "currency": "GBP",
        },
    )
    assert auth_resp.json()["decision"] == "APPROVED"
    authorization_id = auth_resp.json()["authorization_id"]

    # Different user
    attacker_id = str(uuid4())
    await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer dev:{attacker_id}:beneficiary"},
    )

    dispute_resp = await client.post(
        f"/api/v1/transactions/{authorization_id}/dispute",
        json={"reason": "Unauthorized dispute attempt."},
        headers={"Authorization": f"Bearer dev:{attacker_id}:beneficiary"},
    )
    assert dispute_resp.status_code in (403, 404), dispute_resp.text


@pytest.mark.asyncio
async def test_authorize_unknown_card_token(client: AsyncClient) -> None:
    """Unknown card token → DECLINED CARD_NOT_FOUND."""
    resp = await client.post(
        "/api/v1/webhooks/card-processor/authorization",
        json={
            "processor_auth_ref": f"auth-{uuid4()}",
            "card_token": "nonexistent-processor-token",
            "merchant_name": "Test",
            "amount": 1_000,
            "currency": "GBP",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["decision"] == "DECLINED"
    assert body["reason"] == "CARD_NOT_FOUND"
