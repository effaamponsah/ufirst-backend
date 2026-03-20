"""
BankConnectionService — manages sponsor bank account links (AIS flow).

Sponsors link their bank accounts so open banking payments can be initiated
against a specific bank. The account identifier (IBAN / sort code+account)
is stored encrypted with AES-256-GCM.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.encryption import decrypt, encrypt
from app.core.exceptions import NotFound, PermissionDenied
from app.modules.wallet import repository as repo
from app.modules.wallet.events import (
    BankConnectionCreated,
    BankConnectionRevoked,
)
from app.modules.wallet.models import BankConnectionStatus
from app.modules.wallet.openbanking.adapter import PaymentAdapter, get_adapter
from app.modules.wallet.schemas import BankConnectionResponse, StartBankLinkResponse

log = logging.getLogger(__name__)

_DEFAULT_CONSENT_DAYS = 90  # fallback if aggregator doesn't return an expiry


class BankConnectionService:
    def __init__(
        self,
        session: AsyncSession,
        adapter: PaymentAdapter | None = None,
    ) -> None:
        self._session = session
        self._adapter = adapter or get_adapter("open_banking")

    def _redirect_uri(self) -> str:
        from app.config import settings

        return (
            settings.truelayer_redirect_uri
            or f"{settings.app_base_url}/api/v1/webhooks/openbanking/connect-callback"
        )

    async def create_connection_session(
        self, sponsor_id: UUID
    ) -> StartBankLinkResponse:
        """Start an AIS bank link flow. Returns the auth_link for the sponsor."""
        auth_link = await self._adapter.create_connection_session(
            redirect_uri=self._redirect_uri()
        )
        return StartBankLinkResponse(auth_link=auth_link)

    async def complete_connection(
        self, sponsor_id: UUID, code: str
    ) -> BankConnectionResponse:
        """
        Exchange the authorisation code for AIS access, fetch account info,
        encrypt the account identifier, and persist the bank connection.
        """
        bank_info = await self._adapter.complete_connection(
            code=code, redirect_uri=self._redirect_uri()
        )

        encrypted_identifier = encrypt(bank_info.account_identifier)

        # Parse consent_expires_at — fall back to default if absent / unparseable
        consent_expires_at: datetime | None = None
        if bank_info.consent_expires_at:
            try:
                consent_expires_at = datetime.fromisoformat(
                    bank_info.consent_expires_at.replace("Z", "+00:00")
                )
            except ValueError:
                log.warning(
                    "Could not parse consent_expires_at=%r; defaulting to %d days",
                    bank_info.consent_expires_at,
                    _DEFAULT_CONSENT_DAYS,
                )
        if consent_expires_at is None:
            consent_expires_at = datetime.now(timezone.utc) + timedelta(
                days=_DEFAULT_CONSENT_DAYS
            )

        connection = await repo.create_bank_connection(
            self._session,
            sponsor_id=sponsor_id,
            aggregator=self._adapter.__class__.__name__.lower().replace("client", ""),
            external_account_id=bank_info.external_account_id,
            account_identifier_encrypted=encrypted_identifier,
            account_holder_name=bank_info.account_holder_name,
            provider_id=bank_info.provider_id,
            provider_display_name=bank_info.provider_display_name,
            currency=bank_info.currency,
            consent_id=bank_info.consent_id,
            consent_expires_at=consent_expires_at,
        )

        await events.publish(
            BankConnectionCreated(
                connection_id=connection.id,
                sponsor_id=sponsor_id,
                provider_display_name=connection.provider_display_name,
            )
        )

        return BankConnectionResponse.model_validate(connection)

    async def list_connections(self, sponsor_id: UUID) -> list[BankConnectionResponse]:
        connections = await repo.list_bank_connections(self._session, sponsor_id)
        return [BankConnectionResponse.model_validate(c) for c in connections]

    async def revoke_connection(
        self, connection_id: UUID, sponsor_id: UUID
    ) -> None:
        """
        Revoke AIS consent with the aggregator and mark the connection REVOKED.
        Idempotent: returns without error if already revoked.
        """
        connection = await repo.get_bank_connection(self._session, connection_id)
        if connection is None or connection.sponsor_id != sponsor_id:
            raise NotFound("Bank connection not found.")

        if connection.status == BankConnectionStatus.REVOKED:
            return  # already revoked

        if connection.status != BankConnectionStatus.ACTIVE:
            raise PermissionDenied(
                f"Cannot revoke a connection with status '{connection.status.value}'."
            )

        # Best-effort revocation with the aggregator
        try:
            await self._adapter.revoke_consent(connection.consent_id)
        except Exception:
            log.exception(
                "Aggregator consent revocation failed for connection_id=%s; "
                "marking REVOKED locally anyway.",
                connection_id,
            )

        await repo.update_bank_connection_status(
            self._session, connection_id, BankConnectionStatus.REVOKED
        )

        await events.publish(
            BankConnectionRevoked(
                connection_id=connection_id,
                sponsor_id=sponsor_id,
            )
        )
