from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user, require_roles
from app.core.database import get_db
from app.core.exceptions import PermissionDenied
from app.modules.identity.service import IdentityService
from app.modules.wallet.openbanking.connections import BankConnectionService
from app.modules.wallet.openbanking.payments import PaymentInitiationService
from app.modules.wallet.schemas import (
    BankConnectionResponse,
    FundingInitiateResponse,
    FundingStatusResponse,
    FundingTransferResponse,
    InitiateFundingRequest,
    LedgerEntryResponse,
    StartBankLinkResponse,
    UpdateFundingStateRequest,
    WalletResponse,
)
from app.modules.wallet.service import WalletService

router = APIRouter(tags=["wallet"])


def _get_wallet_service(session: AsyncSession = Depends(get_db)) -> WalletService:
    return WalletService(session)


def _get_identity_service(
    session: AsyncSession = Depends(get_db),
) -> IdentityService:
    return IdentityService(session)


def _get_payment_initiation_service(
    session: AsyncSession = Depends(get_db),
) -> PaymentInitiationService:
    return PaymentInitiationService(session, IdentityService(session))


def _get_connection_service(
    session: AsyncSession = Depends(get_db),
) -> BankConnectionService:
    return BankConnectionService(session)


# ---------------------------------------------------------------------------
# Wallets
# ---------------------------------------------------------------------------


@router.get("/wallets/me")
async def get_my_wallet(
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_wallet_service),
) -> WalletResponse:
    return await service.get_wallet_by_owner(current_user.id)


@router.get(
    "/wallets/{wallet_id}",
    dependencies=[Depends(require_roles("admin", "ops_agent", "compliance_officer"))],
)
async def get_wallet(
    wallet_id: UUID,
    service: WalletService = Depends(_get_wallet_service),
) -> WalletResponse:
    return await service.get_wallet(wallet_id)


@router.get("/wallets/me/ledger")
async def get_my_ledger(
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_wallet_service),
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
    payment_svc: PaymentInitiationService = Depends(_get_payment_initiation_service),
    wallet_svc: WalletService = Depends(_get_wallet_service),
) -> FundingInitiateResponse:
    """
    Initiate a funding transfer.  Returns auth_link for the bank redirect
    (open banking) or Stripe client_secret (card).

    Phase 3: FX rate is locked and the aggregator is called.
    auth_link is None for ACH / mobile money (Phase 4+ stubs).
    """
    dest_currency = body.dest_currency or body.source_currency

    # Determine the wallet to fund
    if body.beneficiary_wallet_id is not None:
        wallet_id = body.beneficiary_wallet_id
    else:
        own_wallet = await wallet_svc.get_wallet_by_owner(current_user.id)
        wallet_id = own_wallet.id

    transfer_id, auth_link = await payment_svc.initiate_payment(
        sponsor_id=current_user.id,
        wallet_id=wallet_id,
        payment_method=body.payment_method,
        source_amount=body.source_amount,
        source_currency=body.source_currency,
        dest_currency=dest_currency,
        idempotency_key=idempotency_key,
        beneficiary_wallet_id=body.beneficiary_wallet_id,
        bank_account_id=body.bank_account_id,
    )

    # Fetch the transfer record to build the response
    transfer = await wallet_svc.get_funding_transfer(transfer_id)
    return FundingInitiateResponse(
        funding_transfer_id=transfer.id,
        auth_link=auth_link,
        payment_method=transfer.payment_method,
        payment_state=transfer.payment_state,
        source_amount=transfer.source_amount,
        source_currency=transfer.source_currency,
        dest_amount=transfer.dest_amount,
        dest_currency=transfer.dest_currency,
    )


@router.get("/funding/{transfer_id}/status")
async def get_funding_status(
    transfer_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_wallet_service),
) -> FundingStatusResponse:
    """
    Poll the current payment state of a funding transfer.
    Frontend polls this every 2 seconds while waiting for bank redirect completion.
    """
    transfer = await service.get_funding_transfer(transfer_id)
    if transfer.sponsor_id != current_user.id and current_user.role not in {
        "admin", "ops_agent", "compliance_officer"
    }:
        raise PermissionDenied("Access denied.")
    return FundingStatusResponse.model_validate(transfer)


@router.get("/funding/{transfer_id}")
async def get_funding_transfer(
    transfer_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_wallet_service),
) -> FundingTransferResponse:
    transfer = await service.get_funding_transfer(transfer_id)
    if transfer.sponsor_id != current_user.id and current_user.role not in {
        "admin", "ops_agent", "compliance_officer"
    }:
        raise PermissionDenied("Access denied.")
    return transfer


@router.post(
    "/funding/{transfer_id}/cancel",
    dependencies=[Depends(require_roles("sponsor"))],
)
async def cancel_funding(
    transfer_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: WalletService = Depends(_get_wallet_service),
) -> FundingTransferResponse:
    """Cancel a funding transfer that is still in AWAITING_AUTHORIZATION."""
    transfer = await service.get_funding_transfer(transfer_id)
    if transfer.sponsor_id != current_user.id:
        raise PermissionDenied("You can only cancel your own transfers.")
    from app.modules.wallet.models import PaymentState

    return await service.advance_funding_state(
        transfer_id, new_state=PaymentState.CANCELLED
    )


@router.patch(
    "/funding/{transfer_id}/state",
    dependencies=[Depends(require_roles("admin", "ops_agent"))],
)
async def update_funding_state(
    transfer_id: UUID,
    body: UpdateFundingStateRequest,
    service: WalletService = Depends(_get_wallet_service),
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
    service: WalletService = Depends(_get_wallet_service),
) -> WalletResponse:
    """
    Advance transfer to AWAITING_SETTLEMENT then credit the wallet (COMPLETED).
    In production this is triggered by the provider webhook.
    Exposed here for ops / manual testing.
    """
    return await service.credit_from_funding(transfer_id)


# ---------------------------------------------------------------------------
# Bank connections (Phase 3 — open banking AIS)
# ---------------------------------------------------------------------------


@router.get(
    "/funding/banks",
    dependencies=[Depends(require_roles("sponsor"))],
)
async def list_bank_connections(
    current_user: CurrentUser = Depends(get_current_user),
    service: BankConnectionService = Depends(_get_connection_service),
) -> list[BankConnectionResponse]:
    """List the sponsor's active linked bank accounts."""
    return await service.list_connections(current_user.id)


@router.post(
    "/funding/banks/link",
    status_code=201,
    dependencies=[Depends(require_roles("sponsor"))],
)
async def start_bank_link(
    service: BankConnectionService = Depends(_get_connection_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> StartBankLinkResponse:
    """Start the bank account link flow. Returns auth_link for the sponsor to visit."""
    return await service.create_connection_session(current_user.id)


@router.post(
    "/funding/banks/complete",
    status_code=201,
    dependencies=[Depends(require_roles("sponsor"))],
)
async def complete_bank_link(
    body: dict,  # type: ignore[type-arg]
    current_user: CurrentUser = Depends(get_current_user),
    service: BankConnectionService = Depends(_get_connection_service),
) -> BankConnectionResponse:
    """
    Complete the bank account link after the sponsor returns from the bank
    redirect.  The mobile app intercepts the deep link, extracts the
    authorisation code, and calls this endpoint.

    Body: { "code": "<aggregator_auth_code>" }
    """
    code: str = body.get("code", "")
    if not code:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Missing 'code' in request body.")
    return await service.complete_connection(current_user.id, code)


@router.delete(
    "/funding/banks/{connection_id}",
    status_code=204,
    dependencies=[Depends(require_roles("sponsor"))],
)
async def revoke_bank_connection(
    connection_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: BankConnectionService = Depends(_get_connection_service),
) -> None:
    """Revoke AIS consent and deactivate the linked bank account."""
    await service.revoke_connection(connection_id, current_user.id)
