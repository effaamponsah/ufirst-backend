"""
Transaction module routes.

Authenticated endpoints for listing transactions and opening disputes.
Webhook endpoints for the card processor (no auth — signature-based verification).
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.exceptions import WebhookSignatureInvalid
from app.modules.transaction.schemas import (
    AuthorizationDecisionResponse,
    AuthorizationResponse,
    AuthorizationWebhookPayload,
    ClearingWebhookPayload,
    ClearingResponse,
    DisputeResponse,
    OpenDisputeRequest,
    ReversalWebhookPayload,
)
from app.modules.transaction.service import TransactionService
from app.modules.wallet.service import WalletService

log = logging.getLogger(__name__)

router = APIRouter(tags=["transactions"])


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_transaction_service(
    session: AsyncSession = Depends(get_db),
) -> TransactionService:
    return TransactionService(session)


def _get_wallet_service(
    session: AsyncSession = Depends(get_db),
) -> WalletService:
    return WalletService(session)


# ---------------------------------------------------------------------------
# Webhook signature verification helper
# ---------------------------------------------------------------------------


def _verify_processor_signature(
    x_processor_signature: str | None,
) -> None:
    """
    Verify the card processor webhook signature.

    In dev mode: log a warning if absent but proceed.
    In production: raise 401 if the header is missing.

    Real HMAC verification can be added here when the processor integration
    is finalized.
    """
    if x_processor_signature is None:
        if settings.dev_mode:
            log.warning(
                "X-Processor-Signature header missing — proceeding in dev mode"
            )
        else:
            raise WebhookSignatureInvalid(
                "X-Processor-Signature header is required."
            )


# ---------------------------------------------------------------------------
# Authenticated transaction endpoints
# ---------------------------------------------------------------------------


@router.get("/transactions/")
async def list_transactions(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    txn_svc: TransactionService = Depends(_get_transaction_service),
    wallet_svc: WalletService = Depends(_get_wallet_service),
) -> list[AuthorizationResponse]:
    """
    List transactions for the current user's wallet.

    Sponsors see their own wallet's transactions. Beneficiaries see their own.
    """
    wallet = await wallet_svc.get_wallet_by_owner(current_user.id)
    return await txn_svc.list_transactions(
        wallet.id, page=page, per_page=per_page
    )


@router.get("/transactions/{authorization_id}")
async def get_transaction(
    authorization_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    txn_svc: TransactionService = Depends(_get_transaction_service),
) -> AuthorizationResponse:
    """Retrieve a single authorization. Only accessible by the wallet owner."""
    return await txn_svc.get_authorization(authorization_id, current_user.id)


@router.post("/transactions/{authorization_id}/dispute", status_code=201)
async def open_dispute(
    authorization_id: UUID,
    body: OpenDisputeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    txn_svc: TransactionService = Depends(_get_transaction_service),
) -> DisputeResponse:
    """Open a dispute for a transaction. Only the wallet owner may dispute."""
    return await txn_svc.open_dispute(
        authorization_id, current_user.id, body.reason
    )


# ---------------------------------------------------------------------------
# Webhook endpoints — no auth, processor signature verification
# ---------------------------------------------------------------------------


@router.post(
    "/webhooks/card-processor/authorization",
    response_model=AuthorizationDecisionResponse,
)
async def card_processor_authorization(
    payload: AuthorizationWebhookPayload,
    request: Request,
    x_processor_signature: str | None = Header(default=None),
    txn_svc: TransactionService = Depends(_get_transaction_service),
) -> AuthorizationDecisionResponse:
    """
    Synchronous card authorization webhook.

    The card processor sends this when a card is swiped at a POS terminal.
    We must respond within ~2 seconds with APPROVED or DECLINED.
    """
    _verify_processor_signature(x_processor_signature)
    return await txn_svc.authorize(payload)


@router.post(
    "/webhooks/card-processor/clearing",
    status_code=200,
)
async def card_processor_clearing(
    payload: ClearingWebhookPayload,
    x_processor_signature: str | None = Header(default=None),
    txn_svc: TransactionService = Depends(_get_transaction_service),
) -> ClearingResponse:
    """
    Card clearing webhook.

    Called when the processor confirms a transaction has settled.
    Converts the authorization hold to a permanent DEBIT ledger entry.
    """
    _verify_processor_signature(x_processor_signature)
    return await txn_svc.process_clearing(payload)


@router.post(
    "/webhooks/card-processor/reversal",
    status_code=204,
)
async def card_processor_reversal(
    payload: ReversalWebhookPayload,
    x_processor_signature: str | None = Header(default=None),
    txn_svc: TransactionService = Depends(_get_transaction_service),
) -> None:
    """
    Card reversal webhook.

    Called when an authorization is cancelled before clearing.
    Releases the reserved hold back to available balance.
    """
    _verify_processor_signature(x_processor_signature)
    await txn_svc.process_reversal(payload)
