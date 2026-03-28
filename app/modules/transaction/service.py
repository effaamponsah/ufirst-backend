"""
TransactionService — public interface for the transaction module.

Handles card authorization holds, clearings, reversals, and disputes.
All other modules MUST use this service. Never import from
transaction.repository or transaction.models directly from outside this module.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.exceptions import (
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
)
from app.modules.card import repository as card_repo
from app.modules.card.models import CardStatus
from app.modules.transaction import repository as repo
from app.modules.transaction.events import (
    TransactionAuthorized,
    TransactionCleared,
    TransactionDeclined,
    TransactionReversed,
)
from app.modules.transaction.models import AuthorizationStatus
from app.modules.transaction.schemas import (
    AuthorizationDecisionResponse,
    AuthorizationResponse,
    AuthorizationWebhookPayload,
    ClearingResponse,
    ClearingWebhookPayload,
    DisputeResponse,
    ReversalWebhookPayload,
)
from app.modules.wallet.service import WalletService

log = logging.getLogger(__name__)


class TransactionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    async def authorize(
        self, payload: AuthorizationWebhookPayload
    ) -> AuthorizationDecisionResponse:
        """
        Process a card authorization request from the card processor.

        Creates an Authorization record for every decision (approved or declined)
        so there is always a complete audit trail. If approved, reserves the amount
        from the wallet's available balance.

        Must complete within ~2 seconds as this is a synchronous processor callback.
        """
        wallet_svc = WalletService(self._session)

        # 1. Look up card by processor token
        card = await card_repo.get_card_by_processor_token(
            self._session, payload.card_token
        )
        if card is None:
            auth = await repo.create_authorization(
                self._session,
                card_id=_nil_uuid(),
                wallet_id=_nil_uuid(),
                merchant_name=payload.merchant_name,
                merchant_category_code=payload.merchant_category_code,
                amount=payload.amount,
                currency=payload.currency,
                status=AuthorizationStatus.DECLINED,
                processor_auth_ref=payload.processor_auth_ref,
                decline_reason="CARD_NOT_FOUND",
            )
            return AuthorizationDecisionResponse(
                decision="DECLINED",
                reason="CARD_NOT_FOUND",
                authorization_id=auth.id,
            )

        # 2. Card must be ACTIVE
        if card.status != CardStatus.ACTIVE:
            auth = await repo.create_authorization(
                self._session,
                card_id=card.id,
                wallet_id=card.wallet_id,
                merchant_name=payload.merchant_name,
                merchant_category_code=payload.merchant_category_code,
                amount=payload.amount,
                currency=payload.currency,
                status=AuthorizationStatus.DECLINED,
                processor_auth_ref=payload.processor_auth_ref,
                decline_reason="CARD_INACTIVE",
            )
            await events.publish(
                TransactionDeclined(
                    card_id=card.id,
                    wallet_id=card.wallet_id,
                    amount=payload.amount,
                    currency=payload.currency,
                    merchant_name=payload.merchant_name,
                    reason="CARD_INACTIVE",
                )
            )
            return AuthorizationDecisionResponse(
                decision="DECLINED",
                reason="CARD_INACTIVE",
                authorization_id=auth.id,
            )

        # 3. Spending controls
        controls = card.spending_controls or {}

        # 3a. Daily limit
        daily_limit = controls.get("daily_limit")
        if daily_limit is not None:
            daily_total = await repo.get_daily_authorized_total(
                self._session, card.id
            )
            if daily_total + payload.amount > daily_limit:
                auth = await repo.create_authorization(
                    self._session,
                    card_id=card.id,
                    wallet_id=card.wallet_id,
                    merchant_name=payload.merchant_name,
                    merchant_category_code=payload.merchant_category_code,
                    amount=payload.amount,
                    currency=payload.currency,
                    status=AuthorizationStatus.DECLINED,
                    processor_auth_ref=payload.processor_auth_ref,
                    decline_reason="DAILY_LIMIT_EXCEEDED",
                )
                await events.publish(
                    TransactionDeclined(
                        card_id=card.id,
                        wallet_id=card.wallet_id,
                        amount=payload.amount,
                        currency=payload.currency,
                        merchant_name=payload.merchant_name,
                        reason="DAILY_LIMIT_EXCEEDED",
                    )
                )
                return AuthorizationDecisionResponse(
                    decision="DECLINED",
                    reason="DAILY_LIMIT_EXCEEDED",
                    authorization_id=auth.id,
                )

        # 3b. Category allowlist
        allowed_categories = controls.get("categories")
        if allowed_categories is not None and payload.merchant_category_code is not None:
            if payload.merchant_category_code not in allowed_categories:
                auth = await repo.create_authorization(
                    self._session,
                    card_id=card.id,
                    wallet_id=card.wallet_id,
                    merchant_name=payload.merchant_name,
                    merchant_category_code=payload.merchant_category_code,
                    amount=payload.amount,
                    currency=payload.currency,
                    status=AuthorizationStatus.DECLINED,
                    processor_auth_ref=payload.processor_auth_ref,
                    decline_reason="CATEGORY_NOT_ALLOWED",
                )
                await events.publish(
                    TransactionDeclined(
                        card_id=card.id,
                        wallet_id=card.wallet_id,
                        amount=payload.amount,
                        currency=payload.currency,
                        merchant_name=payload.merchant_name,
                        reason="CATEGORY_NOT_ALLOWED",
                    )
                )
                return AuthorizationDecisionResponse(
                    decision="DECLINED",
                    reason="CATEGORY_NOT_ALLOWED",
                    authorization_id=auth.id,
                )

        # 3c. Merchant allowlist
        merchant_allowlist = controls.get("merchant_allowlist")
        if merchant_allowlist is not None:
            if payload.merchant_name not in merchant_allowlist:
                auth = await repo.create_authorization(
                    self._session,
                    card_id=card.id,
                    wallet_id=card.wallet_id,
                    merchant_name=payload.merchant_name,
                    merchant_category_code=payload.merchant_category_code,
                    amount=payload.amount,
                    currency=payload.currency,
                    status=AuthorizationStatus.DECLINED,
                    processor_auth_ref=payload.processor_auth_ref,
                    decline_reason="MERCHANT_NOT_ALLOWED",
                )
                await events.publish(
                    TransactionDeclined(
                        card_id=card.id,
                        wallet_id=card.wallet_id,
                        amount=payload.amount,
                        currency=payload.currency,
                        merchant_name=payload.merchant_name,
                        reason="MERCHANT_NOT_ALLOWED",
                    )
                )
                return AuthorizationDecisionResponse(
                    decision="DECLINED",
                    reason="MERCHANT_NOT_ALLOWED",
                    authorization_id=auth.id,
                )

        # 4. Check wallet exists and is ACTIVE + sufficient balance
        try:
            wallet = await wallet_svc.get_wallet(card.wallet_id)
        except NotFound:
            auth = await repo.create_authorization(
                self._session,
                card_id=card.id,
                wallet_id=card.wallet_id,
                merchant_name=payload.merchant_name,
                merchant_category_code=payload.merchant_category_code,
                amount=payload.amount,
                currency=payload.currency,
                status=AuthorizationStatus.DECLINED,
                processor_auth_ref=payload.processor_auth_ref,
                decline_reason="WALLET_NOT_FOUND",
            )
            return AuthorizationDecisionResponse(
                decision="DECLINED",
                reason="WALLET_NOT_FOUND",
                authorization_id=auth.id,
            )

        if wallet.available_balance < payload.amount:
            auth = await repo.create_authorization(
                self._session,
                card_id=card.id,
                wallet_id=card.wallet_id,
                merchant_name=payload.merchant_name,
                merchant_category_code=payload.merchant_category_code,
                amount=payload.amount,
                currency=payload.currency,
                status=AuthorizationStatus.DECLINED,
                processor_auth_ref=payload.processor_auth_ref,
                decline_reason="INSUFFICIENT_BALANCE",
            )
            await events.publish(
                TransactionDeclined(
                    card_id=card.id,
                    wallet_id=card.wallet_id,
                    amount=payload.amount,
                    currency=payload.currency,
                    merchant_name=payload.merchant_name,
                    reason="INSUFFICIENT_BALANCE",
                )
            )
            return AuthorizationDecisionResponse(
                decision="DECLINED",
                reason="INSUFFICIENT_BALANCE",
                authorization_id=auth.id,
            )

        # 5. Create the AUTHORIZED record, then reserve balance.
        #    If reserve fails (race condition or wallet state change), downgrade to DECLINED.
        auth = await repo.create_authorization(
            self._session,
            card_id=card.id,
            wallet_id=card.wallet_id,
            merchant_name=payload.merchant_name,
            merchant_category_code=payload.merchant_category_code,
            amount=payload.amount,
            currency=payload.currency,
            status=AuthorizationStatus.AUTHORIZED,
            processor_auth_ref=payload.processor_auth_ref,
        )

        try:
            await wallet_svc.reserve_balance(
                card.wallet_id,
                amount=payload.amount,
                reference_type="authorization",
                reference_id=auth.id,
            )
        except Exception as exc:
            log.warning(
                "reserve_balance failed for auth %s: %s — downgrading to DECLINED",
                auth.id,
                exc,
            )
            auth = await repo.update_authorization_status(
                self._session, auth, new_status=AuthorizationStatus.DECLINED
            )
            auth.decline_reason = "RESERVE_FAILED"
            await self._session.flush()
            return AuthorizationDecisionResponse(
                decision="DECLINED",
                reason="RESERVE_FAILED",
                authorization_id=auth.id,
            )

        await events.publish(
            TransactionAuthorized(
                authorization_id=auth.id,
                card_id=card.id,
                wallet_id=card.wallet_id,
                amount=payload.amount,
                currency=payload.currency,
                merchant_name=payload.merchant_name,
            )
        )

        return AuthorizationDecisionResponse(
            decision="APPROVED",
            authorization_id=auth.id,
        )

    # ------------------------------------------------------------------
    # Clearing
    # ------------------------------------------------------------------

    async def process_clearing(
        self, payload: ClearingWebhookPayload
    ) -> ClearingResponse:
        """
        Convert an authorized hold into a final DEBIT ledger entry.

        Called when the card processor confirms the transaction settled.
        """
        wallet_svc = WalletService(self._session)

        auth = await repo.get_authorization_by_processor_ref(
            self._session, payload.processor_auth_ref
        )
        if auth is None:
            raise NotFound(
                f"Authorization with processor_auth_ref '{payload.processor_auth_ref}' not found."
            )

        if auth.status != AuthorizationStatus.AUTHORIZED:
            raise InvalidStateTransition(
                f"Cannot clear authorization in status '{auth.status}'; "
                "expected 'authorized'.",
                details={"authorization_id": str(auth.id)},
            )

        # Create the clearing record first so we have its ID for the ledger entry reference
        clearing = await repo.create_clearing(
            self._session,
            authorization_id=auth.id,
            cleared_amount=payload.cleared_amount,
            cleared_currency=payload.cleared_currency,
            processor_clearing_ref=payload.processor_clearing_ref,
        )

        # Convert reserve to permanent DEBIT ledger entry
        await wallet_svc.settle_reserve(
            auth.wallet_id,
            amount=payload.cleared_amount,
            reference_type="clearing",
            reference_id=clearing.id,
            description=f"Card clearing {payload.processor_auth_ref[:20]}",
        )

        # Update authorization status
        auth = await repo.update_authorization_status(
            self._session, auth, new_status=AuthorizationStatus.CLEARED
        )

        await events.publish(
            TransactionCleared(
                authorization_id=auth.id,
                card_id=auth.card_id,
                wallet_id=auth.wallet_id,
                cleared_amount=payload.cleared_amount,
                currency=payload.cleared_currency,
            )
        )

        return ClearingResponse.model_validate(clearing)

    # ------------------------------------------------------------------
    # Reversal
    # ------------------------------------------------------------------

    async def process_reversal(self, payload: ReversalWebhookPayload) -> None:
        """
        Release a reserved hold when a card authorization is reversed.

        Called when the processor cancels an authorization before clearing.
        """
        wallet_svc = WalletService(self._session)

        auth = await repo.get_authorization_by_processor_ref(
            self._session, payload.processor_auth_ref
        )
        if auth is None:
            raise NotFound(
                f"Authorization with processor_auth_ref '{payload.processor_auth_ref}' not found."
            )

        if auth.status != AuthorizationStatus.AUTHORIZED:
            raise InvalidStateTransition(
                f"Cannot reverse authorization in status '{auth.status}'; "
                "expected 'authorized'.",
                details={"authorization_id": str(auth.id)},
            )

        # Release the reserved hold back to available balance
        await wallet_svc.release_reserve(
            auth.wallet_id,
            amount=auth.amount,
            reference_type="reversal",
            reference_id=auth.id,
        )

        # Update authorization status
        auth = await repo.update_authorization_status(
            self._session, auth, new_status=AuthorizationStatus.REVERSED
        )

        await events.publish(
            TransactionReversed(
                authorization_id=auth.id,
                card_id=auth.card_id,
                wallet_id=auth.wallet_id,
                amount=auth.amount,
                currency=auth.currency,
            )
        )

    # ------------------------------------------------------------------
    # Disputes
    # ------------------------------------------------------------------

    async def open_dispute(
        self,
        authorization_id: UUID,
        user_id: UUID,
        reason: str,
    ) -> DisputeResponse:
        """Open a dispute for a card transaction. Only the wallet owner may dispute."""
        wallet_svc = WalletService(self._session)

        auth = await repo.get_authorization(self._session, authorization_id)
        if auth is None:
            raise NotFound(f"Authorization {authorization_id} not found.")

        # Check the user owns the wallet this auth is tied to
        try:
            user_wallet = await wallet_svc.get_wallet_by_owner(user_id)
        except NotFound:
            raise PermissionDenied("You do not have a wallet linked to this transaction.")

        if auth.wallet_id != user_wallet.id:
            raise PermissionDenied(
                "You do not have permission to dispute this transaction."
            )

        dispute = await repo.create_dispute(
            self._session,
            authorization_id=authorization_id,
            reason=reason,
        )
        return DisputeResponse.model_validate(dispute)

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    async def list_transactions(
        self,
        wallet_id: UUID,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> list[AuthorizationResponse]:
        """List authorization records for a wallet with pagination."""
        offset = (page - 1) * per_page
        auths = await repo.list_authorizations_for_wallet(
            self._session,
            wallet_id,
            limit=per_page,
            offset=offset,
        )
        return [AuthorizationResponse.model_validate(a) for a in auths]

    async def get_authorization(
        self, authorization_id: UUID, user_id: UUID
    ) -> AuthorizationResponse:
        """Get a single authorization, verifying wallet ownership."""
        wallet_svc = WalletService(self._session)

        auth = await repo.get_authorization(self._session, authorization_id)
        if auth is None:
            raise NotFound(f"Authorization {authorization_id} not found.")

        try:
            user_wallet = await wallet_svc.get_wallet_by_owner(user_id)
        except NotFound:
            raise PermissionDenied("You do not have a wallet.")

        if auth.wallet_id != user_wallet.id:
            raise PermissionDenied("Access denied.")

        return AuthorizationResponse.model_validate(auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nil_uuid() -> UUID:
    """Return the nil UUID used as a placeholder for unknown card/wallet IDs."""
    return UUID("00000000-0000-0000-0000-000000000000")
