from __future__ import annotations


class UFirstError(Exception):
    """Base for all domain exceptions."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(self, message: str = "", details: dict | None = None) -> None:
        self.message = message or self.code
        self.details = details or {}
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# 400-range
# ---------------------------------------------------------------------------


class ValidationError(UFirstError):
    code = "VALIDATION_ERROR"
    http_status = 422


class IdempotencyConflict(UFirstError):
    """A different request body was submitted with an already-used idempotency key."""

    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409


class InvalidStateTransition(UFirstError):
    code = "INVALID_STATE_TRANSITION"
    http_status = 409


class InsufficientBalance(UFirstError):
    code = "INSUFFICIENT_BALANCE"
    http_status = 422


class FXRateExpired(UFirstError):
    """FX rate lock expired before the payment was settled."""

    code = "FX_RATE_EXPIRED"
    http_status = 409


class DuplicateIdempotencyKey(UFirstError):
    code = "DUPLICATE_IDEMPOTENCY_KEY"
    http_status = 409


# ---------------------------------------------------------------------------
# 401 / 403
# ---------------------------------------------------------------------------


class AuthenticationError(UFirstError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class PermissionDenied(UFirstError):
    code = "PERMISSION_DENIED"
    http_status = 403


class KYCRequired(UFirstError):
    code = "KYC_REQUIRED"
    http_status = 403


class AccountSuspended(UFirstError):
    code = "ACCOUNT_SUSPENDED"
    http_status = 403


# ---------------------------------------------------------------------------
# 404
# ---------------------------------------------------------------------------


class NotFound(UFirstError):
    code = "NOT_FOUND"
    http_status = 404


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


class ComplianceRejected(UFirstError):
    """AML/sanctions screening failed for a financial operation."""

    code = "COMPLIANCE_REJECTED"
    http_status = 422


class SanctionsMatch(ComplianceRejected):
    code = "SANCTIONS_MATCH"


class VelocityLimitExceeded(ComplianceRejected):
    code = "VELOCITY_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# External integrations
# ---------------------------------------------------------------------------


class AggregatorError(UFirstError):
    """Open banking aggregator returned an unexpected error."""

    code = "AGGREGATOR_ERROR"
    http_status = 502


class CardProcessorError(UFirstError):
    code = "CARD_PROCESSOR_ERROR"
    http_status = 502


class WebhookSignatureInvalid(UFirstError):
    code = "WEBHOOK_SIGNATURE_INVALID"
    http_status = 401
