from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.wallet.models import (
    EntryType,
    FundingTransfer,
    LedgerEntry,
    PaymentMethod,
    PaymentState,
    Wallet,
    WalletStatus,
)


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------


async def get_wallet(session: AsyncSession, wallet_id: UUID) -> Wallet | None:
    result = await session.execute(select(Wallet).where(Wallet.id == wallet_id))
    return result.scalar_one_or_none()


async def get_wallet_by_owner(session: AsyncSession, owner_id: UUID) -> Wallet | None:
    result = await session.execute(select(Wallet).where(Wallet.owner_id == owner_id))
    return result.scalar_one_or_none()


async def create_wallet(
    session: AsyncSession,
    *,
    owner_id: UUID,
    currency: str,
) -> Wallet:
    wallet = Wallet(
        owner_id=owner_id,
        currency=currency,
        available_balance=0,
        reserved_balance=0,
        status=WalletStatus.ACTIVE,
    )
    session.add(wallet)
    await session.flush()
    return wallet


async def credit_wallet(
    session: AsyncSession,
    wallet: Wallet,
    *,
    amount: int,
    reference_type: str,
    reference_id: UUID,
    description: str | None = None,
) -> LedgerEntry:
    """Credit available_balance and append a CREDIT ledger entry. Caller owns the transaction."""
    wallet.available_balance += amount
    entry = LedgerEntry(
        wallet_id=wallet.id,
        entry_type=EntryType.CREDIT,
        amount=amount,
        currency=wallet.currency,
        balance_after=wallet.available_balance,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
    )
    session.add(entry)
    await session.flush()
    return entry


async def debit_wallet(
    session: AsyncSession,
    wallet: Wallet,
    *,
    amount: int,
    reference_type: str,
    reference_id: UUID,
    description: str | None = None,
) -> LedgerEntry:
    """Debit available_balance and append a DEBIT ledger entry. Caller must check balance first."""
    wallet.available_balance -= amount
    entry = LedgerEntry(
        wallet_id=wallet.id,
        entry_type=EntryType.DEBIT,
        amount=amount,
        currency=wallet.currency,
        balance_after=wallet.available_balance,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_ledger_entries(
    session: AsyncSession,
    wallet_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[LedgerEntry]:
    result = await session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.wallet_id == wallet_id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Funding transfers
# ---------------------------------------------------------------------------


async def get_funding_transfer(
    session: AsyncSession, transfer_id: UUID
) -> FundingTransfer | None:
    result = await session.execute(
        select(FundingTransfer).where(FundingTransfer.id == transfer_id)
    )
    return result.scalar_one_or_none()


async def get_funding_transfer_by_idempotency_key(
    session: AsyncSession, sponsor_id: UUID, idempotency_key: str
) -> FundingTransfer | None:
    result = await session.execute(
        select(FundingTransfer).where(
            FundingTransfer.sponsor_id == sponsor_id,
            FundingTransfer.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def create_funding_transfer(
    session: AsyncSession,
    *,
    wallet_id: UUID,
    sponsor_id: UUID,
    payment_method: PaymentMethod,
    source_amount: int,
    source_currency: str,
    dest_amount: int,
    dest_currency: str,
    fx_rate: Decimal,
    fee_amount: int,
    idempotency_key: str,
) -> FundingTransfer:
    transfer = FundingTransfer(
        wallet_id=wallet_id,
        sponsor_id=sponsor_id,
        payment_method=payment_method,
        payment_state=PaymentState.INITIATED,
        source_amount=source_amount,
        source_currency=source_currency,
        dest_amount=dest_amount,
        dest_currency=dest_currency,
        fx_rate=fx_rate,
        fee_amount=fee_amount,
        idempotency_key=idempotency_key,
    )
    session.add(transfer)
    await session.flush()
    return transfer


async def update_funding_transfer_state(
    session: AsyncSession,
    transfer: FundingTransfer,
    *,
    new_state: PaymentState,
    external_payment_ref: str | None = None,
    failure_reason: str | None = None,
) -> FundingTransfer:
    transfer.payment_state = new_state
    transfer.payment_state_changed_at = datetime.now(timezone.utc)
    if external_payment_ref is not None:
        transfer.external_payment_ref = external_payment_ref
    if failure_reason is not None:
        transfer.failure_reason = failure_reason
    await session.flush()
    await session.refresh(transfer)
    return transfer
