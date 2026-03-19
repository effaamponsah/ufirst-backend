from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.exceptions import PermissionDenied
from app.modules.identity.service import IdentityService
from app.modules.wallet.schemas import (
    FundingTransferResponse,
    InitiateFundingRequest,
    LedgerEntryResponse,
    UpdateFundingStateRequest,
    WalletResponse,
)
from app.modules.wallet.service import WalletService

router = APIRouter(tags=["wallet"])


def _get_service(session: AsyncSession = Depends(get_db)) -> WalletService:
    return WalletService(session)


def _get_identity_service(session: AsyncSession = Depends(get_db)) -> IdentityService:
    return IdentityService(session)


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------


@router.get("/wallets/me")
async def get_my_wallet(
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_service),
) -> WalletResponse:
    return await service.get_wallet_by_owner(current_user.id)


@router.get(
    "/wallets/{wallet_id}",
    dependencies=[Depends(require_roles("admin", "ops_agent", "compliance_officer"))],
)
async def get_wallet(
    wallet_id: UUID,
    service: WalletService = Depends(_get_service),
) -> WalletResponse:
    return await service.get_wallet(wallet_id)


@router.get("/wallets/me/ledger")
async def get_my_ledger(
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_service),
    limit: int = 50,
    offset: int = 0,
) -> list[LedgerEntryResponse]:
    wallet = await service.get_wallet_by_owner(current_user.id)
    return await service.get_ledger(wallet.id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Funding transfers
# ---------------------------------------------------------------------------


@router.post(
    "/funding/initiate",
    status_code=201,
    dependencies=[Depends(require_roles("sponsor"))],
)
async def initiate_funding(
    body: InitiateFundingRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_service),
    identity_service: IdentityService = Depends(_get_identity_service),
) -> FundingTransferResponse:
    if body.beneficiary_wallet_id is not None:
        # Funding a beneficiary's wallet — verify an active link exists
        beneficiary_wallet = await service.get_wallet(body.beneficiary_wallet_id)
        await identity_service.verify_sponsor_beneficiary_link(
            current_user.id, beneficiary_wallet.owner_id
        )
        wallet_id = body.beneficiary_wallet_id
    else:
        # Funding own wallet
        wallet = await service.get_wallet_by_owner(current_user.id)
        wallet_id = wallet.id

    return await service.initiate_funding(
        wallet_id=wallet_id,
        sponsor_id=current_user.id,
        payment_method=body.payment_method,
        source_amount=body.source_amount,
        source_currency=body.source_currency,
        # In Phase 2 we use 1:1 rate and same currency by default;
        # FX is introduced in Phase 3 with the open-banking integration.
        dest_amount=body.source_amount,
        dest_currency=body.source_currency,
        fx_rate=Decimal("1.0"),
        fee_amount=0,
        idempotency_key=idempotency_key,
    )


@router.get("/funding/{transfer_id}")
async def get_funding_transfer(
    transfer_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_service),
) -> FundingTransferResponse:
    transfer = await service.get_funding_transfer(transfer_id)
    if transfer.sponsor_id != current_user.id and current_user.role not in {
        "admin", "ops_agent", "compliance_officer"
    }:
        raise PermissionDenied("Access denied.")
    return transfer


@router.patch(
    "/funding/{transfer_id}/state",
    dependencies=[Depends(require_roles("admin", "ops_agent"))],
)
async def update_funding_state(
    transfer_id: UUID,
    body: UpdateFundingStateRequest,
    service: WalletService = Depends(_get_service),
) -> FundingTransferResponse:
    return await service.advance_funding_state(
        transfer_id,
        new_state=body.payment_state,
        external_payment_ref=body.external_payment_ref,
        failure_reason=body.failure_reason,
    )


@router.post(
    "/funding/{transfer_id}/complete",
    dependencies=[Depends(require_roles("admin", "ops_agent"))],
)
async def complete_funding(
    transfer_id: UUID,
    service: WalletService = Depends(_get_service),
) -> WalletResponse:
    """
    Advance transfer to AWAITING_SETTLEMENT then credit the wallet (COMPLETED).
    In production this is triggered by the payment provider webhook (Phase 3).
    Exposed here for ops/testing purposes.
    """
    return await service.credit_from_funding(transfer_id)
