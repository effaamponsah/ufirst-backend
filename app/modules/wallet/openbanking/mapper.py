"""
OpenBankingMapper — isolates TrueLayer field names from internal models.

A provider switch only requires updating this class and TrueLayerClient.
"""

from __future__ import annotations


class OpenBankingMapper:
    # TrueLayer payment status → normalised internal status
    PAYMENT_STATUS_MAP: dict[str, str] = {
        "authorization_required": "pending",
        "authorizing": "pending",
        "authorized": "pending",
        "executed": "executed",
        "settled": "executed",   # treat settled same as executed
        "failed": "failed",
        "rejected": "rejected",
    }

    # TrueLayer webhook event type → normalised internal event type
    WEBHOOK_EVENT_MAP: dict[str, str] = {
        "payment_executed": "payment_executed",
        "payment_settled": "payment_executed",
        "payment_failed": "payment_failed",
        "payment_rejected": "payment_failed",
        "payment_authorized": "payment_pending",
        "payment_authorization_required": "payment_pending",
        "payment_authorizing": "payment_pending",
    }

    @classmethod
    def payment_status(cls, raw_status: str) -> str:
        return cls.PAYMENT_STATUS_MAP.get(raw_status.lower(), "pending")

    @classmethod
    def webhook_event_type(cls, raw_type: str) -> str:
        return cls.WEBHOOK_EVENT_MAP.get(raw_type.lower(), "payment_pending")

    @classmethod
    def payment_from_initiate_response(cls, data: dict) -> tuple[str, str]:
        """
        Return (payment_id, auth_link) from a TrueLayer POST /v3/payments response.
        """
        payment_id: str = data["id"]
        # TrueLayer v3: auth link is nested inside authorization_flow.actions.next
        auth_link: str = (
            data.get("authorization_flow", {})
            .get("actions", {})
            .get("next", {})
            .get("uri", "")
        )
        return payment_id, auth_link

    @classmethod
    def status_from_get_payment(cls, data: dict) -> tuple[str, str | None]:
        """Return (normalised_status, failure_reason) from GET /v3/payments/{id}."""
        raw = data.get("status", "")
        normalised = cls.payment_status(raw)
        failure = data.get("failure_stage") or data.get("failure_reason")
        return normalised, failure

    @classmethod
    def webhook_event_from_payload(
        cls, data: dict
    ) -> tuple[str, str, str, str | None]:
        """
        Return (normalised_event_type, payment_id, bank_status, failure_reason)
        from a TrueLayer webhook payload.
        """
        raw_type: str = data.get("type", "")
        # Payload may be nested under "payment" or at top level
        payload = data.get("payment", data)
        payment_id: str = payload.get("id") or data.get("payment_id", "")
        bank_status: str = payload.get("status", raw_type)
        failure_reason: str | None = payload.get("failure_reason") or payload.get(
            "failure_stage"
        )
        return cls.webhook_event_type(raw_type), payment_id, bank_status, failure_reason
