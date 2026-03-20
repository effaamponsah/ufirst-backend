"""
WalletService — public interface for the wallet module.

All other modules MUST use this service to read wallet data or perform
financial operations. Never import from wallet.repository or wallet.models
directly from outside this module.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.exceptions import (
    IdempotencyConflict,
    InsufficientBalance,
    InvalidStateTransition,
    NotFound,
    ValidationError,
)
from app.modules.wallet import repository as repo
from app.modules.wallet.events import WalletCreated, WalletDebited, WalletFunded
from app.modules.wallet.models import PaymentMethod, PaymentState, WalletStatus
from app.modules.wallet.schemas import (
    FundingTransferResponse,
    LedgerEntryResponse,
    WalletResponse,
)

# Valid state transitions for funding transfers
_VALID_TRANSITIONS: dict[PaymentState, set[PaymentState]] = {
    PaymentState.INITIATED: {
        PaymentState.AWAITING_AUTHORIZATION,
        PaymentState.CANCELLED,
        PaymentState.FAILED,
    },
    PaymentState.AWAITING_AUTHORIZATION: {
        PaymentState.AUTHORIZING,
        PaymentState.EXPIRED,
        PaymentState.CANCELLED,
    },
    PaymentState.AUTHORIZING: {
        PaymentState.AWAITING_SETTLEMENT,
        PaymentState.FAILED,
    },
    PaymentState.AWAITING_SETTLEMENT: {
        PaymentState.COMPLETED,
        PaymentState.FAILED,
    },
    PaymentState.COMPLETED: set(),
    PaymentState.FAILED: set(),
    PaymentState.EXPIRED: set(),
    PaymentState.CANCELLED: set(),
}


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Wallet CRUD
    # ------------------------------------------------------------------

    async def get_wallet(self, wallet_id: UUID) -> WalletResponse:
        wallet = await repo.get_wallet(self._session, wallet_id)
        if wallet is None:
            raise NotFound(f"Wallet {wallet_id} not found.")
        return WalletResponse.model_validate(wallet)

    async def get_wallet_by_owner(self, owner_id: UUID) -> WalletResponse:
        wallet = await repo.get_wallet_by_owner(self._session, owner_id)
        if wallet is None:
            raise NotFound(f"No wallet found for owner {owner_id}.")
        return WalletResponse.model_validate(wallet)

    async def create_wallet(self, *, owner_id: UUID, currency: str) -> WalletResponse:
        # Idempotent — return existing wallet if already created
        existing = await repo.get_wallet_by_owner(self._session, owner_id)
        if existing is not None:
            return WalletResponse.model_validate(existing)

        wallet = await repo.create_wallet(self._session, owner_id=owner_id, currency=currency)
        await events.publish(
            WalletCreated(wallet_id=wallet.id, owner_id=owner_id, currency=currency)
        )
        return WalletResponse.model_validate(wallet)

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    async def get_ledger(
        self,
        wallet_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LedgerEntryResponse]:
        wallet = await repo.get_wallet(self._session, wallet_id)
        if wallet is None:
            raise NotFound(f"Wallet {wallet_id} not found.")
        entries = await repo.list_ledger_entries(
            self._session, wallet_id, limit=limit, offset=offset
        )
        return [LedgerEntryResponse.model_validate(e) for e in entries]

    # ------------------------------------------------------------------
    # Funding transfers
    # ------------------------------------------------------------------

    async def initiate_funding(
        self,
        *,
        wallet_id: UUID,
        sponsor_id: UUID,
        payment_method: PaymentMethod,
        source_amount: int,
        source_currency: str,
        dest_amount: int,
        dest_currency: str,
        fx_rate: Decimal,
        fx_rate_locked_until: "datetime | None" = None,
        fee_amount: int,
        idempotency_key: str,
    ) -> FundingTransferResponse:
        if source_amount <= 0 or dest_amount <= 0:
            raise ValidationError(
                "Funding amounts must be positive integers.",
                details={"source_amount": source_amount, "dest_amount": dest_amount},
            )

        # Idempotency check — scoped to this sponsor only
        existing = await repo.get_funding_transfer_by_idempotency_key(
            self._session, sponsor_id, idempotency_key
        )
        if existing is not None:
            # Conflict: same key reused with different request parameters
            if (
                existing.wallet_id != wallet_id
                or existing.source_amount != source_amount
                or existing.source_currency != source_currency
                or existing.payment_method != payment_method
            ):
                raise IdempotencyConflict(
                    "Idempotency-Key reused with different request parameters.",
                    details={"idempotency_key": idempotency_key},
                )
            return FundingTransferResponse.model_validate(existing)

        wallet = await repo.get_wallet(self._session, wallet_id)
        if wallet is None:
            raise NotFound(f"Wallet {wallet_id} not found.")
        if wallet.status != WalletStatus.ACTIVE:
            raise InvalidStateTransition(
                f"Wallet is {wallet.status.value}, not ACTIVE.",
                details={"wallet_id": str(wallet_id)},
            )

        try:
            transfer = await repo.create_funding_transfer(
                self._session,
                wallet_id=wallet_id,
                sponsor_id=sponsor_id,
                payment_method=payment_method,
                source_amount=source_amount,
                source_currency=source_currency,
                dest_amount=dest_amount,
                dest_currency=dest_currency,
                fx_rate=fx_rate,
                fx_rate_locked_until=fx_rate_locked_until,
                fee_amount=fee_amount,
                idempotency_key=idempotency_key,
            )
        except IntegrityError:
            # Concurrent request with the same key won the race — roll back and
            # inspect the winning record before deciding how to respond.
            await self._session.rollback()
            existing = await repo.get_funding_transfer_by_idempotency_key(
                self._session, sponsor_id, idempotency_key
            )
            if existing is None:
                raise  # integrity error from something else; let it propagate
            # The concurrent winner used a different payload — this is a conflict.
            if (
                existing.wallet_id != wallet_id
                or existing.source_amount != source_amount
                or existing.source_currency != source_currency
                or existing.payment_method != payment_method
            ):
                raise IdempotencyConflict(
                    "Idempotency-Key reused with different request parameters.",
                    details={"idempotency_key": idempotency_key},
                )
            return FundingTransferResponse.model_validate(existing)

        return FundingTransferResponse.model_validate(transfer)

    async def get_funding_transfer(self, transfer_id: UUID) -> FundingTransferResponse:
        transfer = await repo.get_funding_transfer(self._session, transfer_id)
        if transfer is None:
            raise NotFound(f"Funding transfer {transfer_id} not found.")
        return FundingTransferResponse.model_validate(transfer)

    async def advance_funding_state(
        self,
        transfer_id: UUID,
        *,
        new_state: PaymentState,
        external_payment_ref: str | None = None,
        failure_reason: str | None = None,
    ) -> FundingTransferResponse:
        transfer = await repo.get_funding_transfer(self._session, transfer_id)
        if transfer is None:
            raise NotFound(f"Funding transfer {transfer_id} not found.")

        allowed = _VALID_TRANSITIONS.get(transfer.payment_state, set())
        if new_state not in allowed:
            raise InvalidStateTransition(
                f"Cannot move funding transfer from {transfer.payment_state.value} to {new_state.value}.",
                details={"transfer_id": str(transfer_id)},
            )

        updated = await repo.update_funding_transfer_state(
            self._session,
            transfer,
            new_state=new_state,
            external_payment_ref=external_payment_ref,
            failure_reason=failure_reason,
        )
        return FundingTransferResponse.model_validate(updated)

    async def credit_from_funding(self, transfer_id: UUID) -> WalletResponse:
        """
        Credit wallet from a completed funding transfer.

        Uses SERIALIZABLE isolation to prevent double-crediting.
        The caller (typically a webhook handler) must NOT have an active
        transaction — this method manages its own.
        """
        # Set SERIALIZABLE isolation for this operation only
        await self._session.execute(
            text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        )

        transfer = await repo.get_funding_transfer(self._session, transfer_id)
        if transfer is None:
            raise NotFound(f"Funding transfer {transfer_id} not found.")

        if transfer.payment_state != PaymentState.AWAITING_SETTLEMENT:
            raise InvalidStateTransition(
                f"Cannot credit wallet: transfer is in state {transfer.payment_state.value}, "
                "expected AWAITING_SETTLEMENT.",
                details={"transfer_id": str(transfer_id)},
            )

        wallet = await repo.get_wallet(self._session, transfer.wallet_id)
        if wallet is None:
            raise NotFound(f"Wallet {transfer.wallet_id} not found.")

        # Credit the wallet
        await repo.credit_wallet(
            self._session,
            wallet,
            amount=transfer.dest_amount,
            reference_type="funding_transfer",
            reference_id=transfer_id,
            description=f"Funding via {transfer.payment_method.value}",
        )

        # Mark transfer COMPLETED
        await repo.update_funding_transfer_state(
            self._session,
            transfer,
            new_state=PaymentState.COMPLETED,
        )

        await events.publish(
            WalletFunded(
                wallet_id=wallet.id,
                amount=transfer.dest_amount,
                currency=wallet.currency,
                funding_transfer_id=transfer_id,
            )
        )

        return WalletResponse.model_validate(wallet)

    # ------------------------------------------------------------------
    # Debit (used by transaction module)
    # ------------------------------------------------------------------

    async def debit_wallet(
        self,
        wallet_id: UUID,
        *,
        amount: int,
        reference_type: str,
        reference_id: UUID,
        description: str | None = None,
    ) -> WalletResponse:
        if amount <= 0:
            raise ValidationError(
                "Debit amount must be a positive integer.",
                details={"amount": amount},
            )
        wallet = await repo.get_wallet(self._session, wallet_id)
        if wallet is None:
            raise NotFound(f"Wallet {wallet_id} not found.")
        if wallet.status != WalletStatus.ACTIVE:
            raise InvalidStateTransition(
                f"Wallet is {wallet.status.value}, not ACTIVE.",
                details={"wallet_id": str(wallet_id)},
            )
        if wallet.available_balance < amount:
            raise InsufficientBalance(
                "Insufficient balance.",
                details={
                    "wallet_id": str(wallet_id),
                    "available": wallet.available_balance,
                    "requested": amount,
                },
            )

        await repo.debit_wallet(
            self._session,
            wallet,
            amount=amount,
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
        )
        await events.publish(
            WalletDebited(
                wallet_id=wallet_id,
                amount=amount,
                currency=wallet.currency,
                reference_type=reference_type,
                reference_id=reference_id,
            )
        )
        return WalletResponse.model_validate(wallet)
