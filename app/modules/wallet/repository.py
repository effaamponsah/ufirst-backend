from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.wallet.models import (
    BankConnectionStatus,
    CardPayment,
    EntryType,
    FundingTransfer,
    LedgerEntry,
    OpenBankingPayment,
    OpenBankingWebhookLog,
    PaymentMethod,
    PaymentState,
    SponsorBankConnection,
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


async def reserve_balance(
    session: AsyncSession,
    wallet: Wallet,
    *,
    amount: int,
    reference_type: str,
    reference_id: UUID,
) -> None:
    """Move amount from available to reserved. No ledger entry — authorization hold only."""
    wallet.available_balance -= amount
    wallet.reserved_balance += amount
    await session.flush()


async def release_reserve(
    session: AsyncSession,
    wallet: Wallet,
    *,
    amount: int,
    reference_type: str,
    reference_id: UUID,
) -> None:
    """Release a reserved hold back to available balance. No ledger entry."""
    wallet.reserved_balance -= amount
    wallet.available_balance += amount
    await session.flush()


async def settle_reserve(
    session: AsyncSession,
    wallet: Wallet,
    *,
    amount: int,
    reference_type: str,
    reference_id: UUID,
    description: str | None = None,
) -> LedgerEntry:
    """Convert reserved hold to a final DEBIT ledger entry at clearing time."""
    wallet.reserved_balance -= amount
    entry = LedgerEntry(
        wallet_id=wallet.id,
        entry_type=EntryType.DEBIT,
        amount=amount,
        currency=wallet.currency,
        # available_balance is unchanged by settle; reflects the post-auth state
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


async def get_funding_transfer_by_idempotency_key_only(
    session: AsyncSession, idempotency_key: str
) -> FundingTransfer | None:
    result = await session.execute(
        select(FundingTransfer).where(
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
    fx_rate_locked_until: datetime | None = None,
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
        fx_rate_locked_until=fx_rate_locked_until,
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


# ---------------------------------------------------------------------------
# Bank connections
# ---------------------------------------------------------------------------


async def get_bank_connection(
    session: AsyncSession, connection_id: UUID
) -> SponsorBankConnection | None:
    result = await session.execute(
        select(SponsorBankConnection).where(SponsorBankConnection.id == connection_id)
    )
    return result.scalar_one_or_none()


async def list_bank_connections(
    session: AsyncSession, sponsor_id: UUID
) -> list[SponsorBankConnection]:
    result = await session.execute(
        select(SponsorBankConnection)
        .where(
            SponsorBankConnection.sponsor_id == sponsor_id,
            SponsorBankConnection.status == BankConnectionStatus.ACTIVE,
        )
        .order_by(SponsorBankConnection.created_at.desc())
    )
    return list(result.scalars().all())


async def create_bank_connection(
    session: AsyncSession,
    *,
    sponsor_id: UUID,
    aggregator: str,
    external_account_id: str,
    account_identifier_encrypted: bytes,
    account_holder_name: str,
    provider_id: str,
    provider_display_name: str,
    currency: str,
    consent_id: str,
    consent_expires_at: datetime | None,
) -> SponsorBankConnection:
    conn = SponsorBankConnection(
        sponsor_id=sponsor_id,
        aggregator=aggregator,
        external_account_id=external_account_id,
        account_identifier_encrypted=account_identifier_encrypted,
        account_holder_name=account_holder_name,
        provider_id=provider_id,
        provider_display_name=provider_display_name,
        currency=currency,
        consent_id=consent_id,
        consent_expires_at=consent_expires_at,
        status=BankConnectionStatus.ACTIVE,
    )
    session.add(conn)
    await session.flush()
    return conn


async def update_bank_connection_status(
    session: AsyncSession,
    connection_id: UUID,
    status: BankConnectionStatus,
) -> SponsorBankConnection | None:
    conn = await get_bank_connection(session, connection_id)
    if conn is None:
        return None
    conn.status = status
    await session.flush()
    return conn


async def list_expiring_connections(
    session: AsyncSession,
    *,
    days_ahead: int = 7,
) -> list[SponsorBankConnection]:
    """Return ACTIVE connections whose consent expires within `days_ahead` days."""
    cutoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    result = await session.execute(
        select(SponsorBankConnection).where(
            SponsorBankConnection.status == BankConnectionStatus.ACTIVE,
            SponsorBankConnection.consent_expires_at.isnot(None),
            SponsorBankConnection.consent_expires_at <= cutoff,
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Open banking payments
# ---------------------------------------------------------------------------


async def create_open_banking_payment(
    session: AsyncSession,
    *,
    funding_transfer_id: UUID,
    aggregator: str,
    aggregator_payment_id: str,
    auth_link: str,
) -> OpenBankingPayment:
    ob = OpenBankingPayment(
        funding_transfer_id=funding_transfer_id,
        aggregator=aggregator,
        aggregator_payment_id=aggregator_payment_id,
        auth_link=auth_link,
    )
    session.add(ob)
    await session.flush()
    return ob


async def get_open_banking_payment_by_transfer_id(
    session: AsyncSession, funding_transfer_id: UUID
) -> OpenBankingPayment | None:
    result = await session.execute(
        select(OpenBankingPayment).where(
            OpenBankingPayment.funding_transfer_id == funding_transfer_id
        )
    )
    return result.scalar_one_or_none()


async def get_open_banking_payment_by_aggregator_id(
    session: AsyncSession, aggregator_payment_id: str
) -> OpenBankingPayment | None:
    result = await session.execute(
        select(OpenBankingPayment).where(
            OpenBankingPayment.aggregator_payment_id == aggregator_payment_id
        )
    )
    return result.scalar_one_or_none()


async def update_open_banking_payment(
    session: AsyncSession,
    ob_payment: OpenBankingPayment,
    *,
    bank_status: str,
    webhook_received_at: datetime | None = None,
    failure_reason: str | None = None,
) -> OpenBankingPayment:
    ob_payment.bank_status = bank_status
    if webhook_received_at is not None:
        ob_payment.webhook_received_at = webhook_received_at
    if failure_reason is not None:
        ob_payment.failure_reason = failure_reason
    await session.flush()
    return ob_payment


# ---------------------------------------------------------------------------
# Webhook log
# ---------------------------------------------------------------------------


async def create_webhook_log(
    session: AsyncSession,
    *,
    aggregator: str,
    event_type: str,
    payload: dict,  # type: ignore[type-arg]
    signature_valid: bool,
) -> OpenBankingWebhookLog:
    log_entry = OpenBankingWebhookLog(
        aggregator=aggregator,
        event_type=event_type,
        payload=payload,
        signature_valid=signature_valid,
    )
    session.add(log_entry)
    await session.flush()
    return log_entry


async def mark_webhook_processed(
    session: AsyncSession,
    log_entry: OpenBankingWebhookLog,
    *,
    error: str | None = None,
) -> None:
    log_entry.processed_at = datetime.now(timezone.utc)
    if error:
        log_entry.processing_error = error
    await session.flush()


# ---------------------------------------------------------------------------
# Safety-net queries (used by Celery beat tasks)
# ---------------------------------------------------------------------------


async def list_pending_settlement_transfers(
    session: AsyncSession,
) -> list[tuple[UUID, str]]:
    """
    Return (transfer_id, aggregator_payment_id) for AWAITING_SETTLEMENT
    transfers where no webhook has arrived yet and the transfer is > 5 min old.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = await session.execute(
        select(FundingTransfer.id, OpenBankingPayment.aggregator_payment_id)
        .join(
            OpenBankingPayment,
            OpenBankingPayment.funding_transfer_id == FundingTransfer.id,
        )
        .where(
            FundingTransfer.payment_state == PaymentState.AWAITING_SETTLEMENT,
            OpenBankingPayment.webhook_received_at.is_(None),
            FundingTransfer.created_at < cutoff,
        )
    )
    return [(row[0], row[1]) for row in result.all()]


async def list_stale_authorization_transfer_ids(
    session: AsyncSession,
) -> list[UUID]:
    """
    Return IDs of transfers in AWAITING_AUTHORIZATION that have not
    progressed for more than 15 minutes (sponsor never completed bank auth).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    result = await session.execute(
        select(FundingTransfer.id).where(
            FundingTransfer.payment_state == PaymentState.AWAITING_AUTHORIZATION,
            FundingTransfer.payment_state_changed_at < cutoff,
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Card payments (Phase 3.8)
# ---------------------------------------------------------------------------


async def create_card_payment(
    session: AsyncSession,
    *,
    funding_transfer_id: UUID,
    stripe_payment_intent_id: str,
    auth_link: str,
    fee_amount: int = 0,
) -> CardPayment:
    cp = CardPayment(
        funding_transfer_id=funding_transfer_id,
        stripe_payment_intent_id=stripe_payment_intent_id,
        auth_link=auth_link,
        fee_amount=fee_amount,
    )
    session.add(cp)
    await session.flush()
    return cp


async def get_card_payment_by_transfer_id(
    session: AsyncSession, funding_transfer_id: UUID
) -> CardPayment | None:
    result = await session.execute(
        select(CardPayment).where(
            CardPayment.funding_transfer_id == funding_transfer_id
        )
    )
    return result.scalar_one_or_none()


async def get_card_payment_by_intent_id(
    session: AsyncSession, intent_id: str
) -> CardPayment | None:
    result = await session.execute(
        select(CardPayment).where(
            CardPayment.stripe_payment_intent_id == intent_id
        )
    )
    return result.scalar_one_or_none()


async def update_card_payment(
    session: AsyncSession,
    cp: CardPayment,
    *,
    card_last4: str | None = None,
    card_brand: str | None = None,
) -> CardPayment:
    if card_last4 is not None:
        cp.card_last4 = card_last4
    if card_brand is not None:
        cp.card_brand = card_brand
    await session.flush()
    return cp
