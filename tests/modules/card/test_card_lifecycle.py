"""
Tests for the card issuance and lifecycle management endpoints.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.modules.card.models import CardStatus
from app.modules.card.service import CardService
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
    from uuid import UUID as _UUID

    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        svc = WalletService(session)
        wallet = await svc.create_wallet(owner_id=_UUID(owner_id), currency=currency)
        await session.commit()
    await engine.dispose()
    return str(wallet.id)


async def _provision_sponsor_with_beneficiary(
    client: AsyncClient,
) -> tuple[str, str]:
    """
    Create a sponsor, a beneficiary via sponsor flow (approved KYC),
    and link them. Returns (sponsor_id, beneficiary_id).
    """
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")

    # Sponsor creates beneficiary
    resp = await client.post(
        "/api/v1/users/me/beneficiaries",
        json={
            "full_name": "Test Beneficiary",
            "phone": "+2348000000001",
            "country": "NG",
            "beneficiary_relationship": "sibling",
        },
        headers={"Authorization": f"Bearer dev:{sponsor_id}:sponsor"},
    )
    assert resp.status_code == 201, resp.text
    beneficiary_id = resp.json()["id"]

    # Mark beneficiary KYC approved directly via service
    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    from app.modules.identity.service import IdentityService

    async with factory() as session:
        identity_svc = IdentityService(session)
        from uuid import UUID as _UUID
        from app.modules.identity.models import KYCStatus
        await identity_svc.update_kyc_status(
            _UUID(beneficiary_id), new_status=KYCStatus.APPROVED, provider_ref="test"
        )
        await session.commit()
    await engine.dispose()

    # Create wallets for both
    await _create_wallet_direct(sponsor_id)
    await _create_wallet_direct(beneficiary_id)

    return sponsor_id, beneficiary_id


def _sponsor_headers(sponsor_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{sponsor_id}:sponsor"}


def _admin_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{user_id}:admin"}


async def _activate_card(client: AsyncClient, card_id: str, admin_id: str) -> None:
    """Activate a PENDING card via the ops endpoint (simulates UP Nigeria dispatch)."""
    resp = await client.post(
        f"/api/v1/cards/{card_id}/activate",
        headers=_admin_headers(admin_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sponsor_can_issue_card(client: AsyncClient) -> None:
    """Happy path: sponsor issues a card for a linked beneficiary."""
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)

    resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["owner_id"] == beneficiary_id
    # Card starts PENDING — physical card has been ordered from UP Nigeria
    assert body["status"] == "pending"
    # processor_token MUST NOT appear in the response
    assert "processor_token" not in body


@pytest.mark.asyncio
async def test_card_issued_with_spending_controls(client: AsyncClient) -> None:
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)

    resp = await client.post(
        "/api/v1/cards/",
        json={
            "beneficiary_id": beneficiary_id,
            "spending_controls": {"daily_limit": 10000},
        },
        headers=_sponsor_headers(sponsor_id),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["spending_controls"]["daily_limit"] == 10000


@pytest.mark.asyncio
async def test_cannot_issue_second_card_for_same_wallet(client: AsyncClient) -> None:
    """One active card per wallet — second issue attempt is rejected."""
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)

    r1 = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    assert r1.status_code == 201

    r2 = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_unlinked_sponsor_cannot_issue_card(client: AsyncClient) -> None:
    """A sponsor who is not linked to the beneficiary is rejected."""
    attacker_id = str(uuid4())
    _, beneficiary_id = await _provision_sponsor_with_beneficiary(client)
    await _create_user(client, attacker_id, "sponsor")
    await _create_wallet_direct(attacker_id)

    resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(attacker_id),
    )
    assert resp.status_code in (403, 404)


@pytest.mark.asyncio
async def test_kyc_not_approved_blocks_card_issuance(client: AsyncClient) -> None:
    """Card issuance is blocked when beneficiary KYC is not approved."""
    sponsor_id = str(uuid4())
    await _create_user(client, sponsor_id, "sponsor")

    # Create beneficiary WITHOUT approving KYC
    resp = await client.post(
        "/api/v1/users/me/beneficiaries",
        json={
            "full_name": "Unverified Beneficiary",
            "phone": "+2348000000002",
            "country": "NG",
            "beneficiary_relationship": "sibling",
        },
        headers=_sponsor_headers(sponsor_id),
    )
    assert resp.status_code == 201
    beneficiary_id = resp.json()["id"]
    await _create_wallet_direct(sponsor_id)
    await _create_wallet_direct(beneficiary_id)

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    assert issue_resp.status_code == 422
    body = issue_resp.json()
    assert body["detail"]["error"]["code"] == "KYC_REQUIRED"


@pytest.mark.asyncio
async def test_activate_card(client: AsyncClient) -> None:
    """Ops activates a PENDING card — simulates UP Nigeria confirming dispatch."""
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)
    admin_id = str(uuid4())

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    assert issue_resp.json()["status"] == "pending"
    card_id = issue_resp.json()["id"]

    await _activate_card(client, card_id, admin_id)


@pytest.mark.asyncio
async def test_sponsor_can_freeze_and_unfreeze_card(client: AsyncClient) -> None:
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)
    admin_id = str(uuid4())

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    card_id = issue_resp.json()["id"]
    await _activate_card(client, card_id, admin_id)  # must be active before freeze

    # Freeze
    freeze_resp = await client.post(
        f"/api/v1/cards/{card_id}/freeze",
        headers=_sponsor_headers(sponsor_id),
    )
    assert freeze_resp.status_code == 200
    assert freeze_resp.json()["status"] == "frozen"

    # Unfreeze
    unfreeze_resp = await client.post(
        f"/api/v1/cards/{card_id}/unfreeze",
        headers=_sponsor_headers(sponsor_id),
    )
    assert unfreeze_resp.status_code == 200
    assert unfreeze_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_cannot_unfreeze_active_card(client: AsyncClient) -> None:
    """Unfreezing an already-active card is a 409."""
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)
    admin_id = str(uuid4())

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    card_id = issue_resp.json()["id"]
    await _activate_card(client, card_id, admin_id)

    resp = await client.post(
        f"/api/v1/cards/{card_id}/unfreeze",
        headers=_sponsor_headers(sponsor_id),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_sponsor_can_cancel_card(client: AsyncClient) -> None:
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)
    admin_id = str(uuid4())

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    card_id = issue_resp.json()["id"]
    await _activate_card(client, card_id, admin_id)

    cancel_resp = await client.delete(
        f"/api/v1/cards/{card_id}",
        headers=_sponsor_headers(sponsor_id),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cannot_cancel_already_cancelled_card(client: AsyncClient) -> None:
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)
    admin_id = str(uuid4())

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    card_id = issue_resp.json()["id"]
    await _activate_card(client, card_id, admin_id)

    await client.delete(f"/api/v1/cards/{card_id}", headers=_sponsor_headers(sponsor_id))

    resp = await client.delete(
        f"/api/v1/cards/{card_id}",
        headers=_sponsor_headers(sponsor_id),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_sponsor_can_update_spending_controls(client: AsyncClient) -> None:
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)
    admin_id = str(uuid4())

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    card_id = issue_resp.json()["id"]
    await _activate_card(client, card_id, admin_id)

    resp = await client.put(
        f"/api/v1/cards/{card_id}/controls",
        json={"spending_controls": {"daily_limit": 50000, "categories": ["grocery"]}},
        headers=_sponsor_headers(sponsor_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["spending_controls"]["daily_limit"] == 50000
    assert body["spending_controls"]["categories"] == ["grocery"]


@pytest.mark.asyncio
async def test_beneficiary_can_read_own_card(client: AsyncClient) -> None:
    sponsor_id, beneficiary_id = await _provision_sponsor_with_beneficiary(client)

    issue_resp = await client.post(
        "/api/v1/cards/",
        json={"beneficiary_id": beneficiary_id},
        headers=_sponsor_headers(sponsor_id),
    )
    card_id = issue_resp.json()["id"]

    get_resp = await client.get(
        f"/api/v1/cards/{card_id}",
        headers={"Authorization": f"Bearer dev:{beneficiary_id}:beneficiary"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == card_id


@pytest.mark.asyncio
async def test_state_machine_service_level() -> None:
    """
    Service-level state machine tests: every valid and invalid transition.
    """
    engine = create_async_engine(settings.async_database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sponsor_id = uuid4()
    beneficiary_id = uuid4()

    async with factory() as session:
        wallet_svc = WalletService(session)
        await wallet_svc.create_wallet(owner_id=beneficiary_id, currency="GBP")
        await session.commit()

    async with factory() as session:
        card_svc = CardService(session)
        card = await card_svc.issue_card(
            wallet_id=(
                await WalletService(session).get_wallet_by_owner(beneficiary_id)
            ).id,
            beneficiary_id=beneficiary_id,
            issued_by=sponsor_id,
        )
        assert card.status == CardStatus.PENDING
        await session.commit()

    card_id = card.id

    # PENDING → ACTIVE (UP Nigeria dispatched the physical card)
    async with factory() as session:
        card_svc = CardService(session)
        card = await card_svc.activate_card(card_id, actor_id=sponsor_id)
        assert card.status == CardStatus.ACTIVE
        await session.commit()

    # Active → Frozen
    async with factory() as session:
        card_svc = CardService(session)
        card = await card_svc.freeze_card(card_id, actor_id=sponsor_id)
        assert card.status == CardStatus.FROZEN
        await session.commit()

    # Frozen → Active
    async with factory() as session:
        card_svc = CardService(session)
        card = await card_svc.unfreeze_card(card_id, actor_id=sponsor_id)
        assert card.status == CardStatus.ACTIVE
        await session.commit()

    # Active → Cancelled
    async with factory() as session:
        card_svc = CardService(session)
        card = await card_svc.cancel_card(card_id, actor_id=sponsor_id)
        assert card.status == CardStatus.CANCELLED
        await session.commit()

    # Cancelled → any (should fail)
    from app.core.exceptions import InvalidStateTransition

    async with factory() as session:
        card_svc = CardService(session)
        with pytest.raises(InvalidStateTransition):
            await card_svc.freeze_card(card_id, actor_id=sponsor_id)

    await engine.dispose()


@pytest.mark.asyncio
async def test_daily_limit_validation() -> None:
    """SpendingControls rejects non-positive daily_limit at schema level."""
    from pydantic import ValidationError as PydanticValidationError

    from app.modules.card.schemas import SpendingControls

    with pytest.raises(PydanticValidationError):
        SpendingControls(daily_limit=0)

    with pytest.raises(PydanticValidationError):
        SpendingControls(daily_limit=-100)

    # Valid
    sc = SpendingControls(daily_limit=10000)
    assert sc.daily_limit == 10000
