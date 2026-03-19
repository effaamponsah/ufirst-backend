"""
JWT authentication via Supabase.

Supports two verification modes controlled by config:
  1. HS256 — SUPABASE_JWT_SECRET is set (simple, default for new projects)
  2. RS256 — SUPABASE_JWKS_URL is set (preferred for production; keys cached locally)

Dev mode (DEV_MODE=true):
  Accepts tokens of the form "Bearer dev:<user_id>:<role>" without hitting
  Supabase at all. Only active when DEBUG mode is also on or DEV_MODE is
  explicitly true. NEVER enable in production.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.core.exceptions import AuthenticationError, PermissionDenied

log = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# JWKS cache (RS256 path)
# ---------------------------------------------------------------------------

_jwks_client: jwt.PyJWKClient | None = None
_jwks_last_refresh: float = 0.0
_JWKS_REFRESH_INTERVAL = 3600  # seconds


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client, _jwks_last_refresh
    now = time.monotonic()
    if _jwks_client is None or (now - _jwks_last_refresh) > _JWKS_REFRESH_INTERVAL:
        _jwks_client = jwt.PyJWKClient(settings.supabase_jwks_url, cache_keys=True)
        _jwks_last_refresh = now
    return _jwks_client


async def warm_jwks_cache() -> None:
    """Pre-fetch JWKS on startup to avoid cold-start latency on the first request."""
    if settings.supabase_jwks_url and not settings.dev_mode:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _get_jwks_client)
            log.info("JWKS cache warmed from %s", settings.supabase_jwks_url)
        except Exception:
            log.warning("Failed to pre-warm JWKS cache; will retry on first request.")


# ---------------------------------------------------------------------------
# Current user dataclass
# ---------------------------------------------------------------------------


@dataclass
class CurrentUser:
    id: UUID
    role: str        # sponsor | beneficiary | vendor_admin | vendor_cashier | ops_agent | compliance_officer | admin
    email: str = ""


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def _verify_token(token: str) -> CurrentUser:
    # ── Dev mode shortcut ─────────────────────────────────────────────────
    if settings.dev_mode and token.startswith("dev:"):
        parts = token.split(":")
        if len(parts) != 3:
            raise AuthenticationError(
                "Dev token must be 'dev:<user_id>:<role>'"
            )
        return CurrentUser(id=UUID(parts[1]), role=parts[2])

    # ── HS256 path ────────────────────────────────────────────────────────
    if settings.supabase_jwt_secret:
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationError("Token has expired.")
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Invalid token: {exc}")
        return _payload_to_user(payload)

    # ── RS256 / JWKS path ─────────────────────────────────────────────────
    if settings.supabase_jwks_url:
        client = _get_jwks_client()
        try:
            signing_key = client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience="authenticated",
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationError("Token has expired.")
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Invalid token: {exc}")
        return _payload_to_user(payload)

    raise AuthenticationError(
        "No JWT verification method configured. "
        "Set SUPABASE_JWT_SECRET or SUPABASE_JWKS_URL."
    )


def _payload_to_user(payload: dict) -> CurrentUser:  # type: ignore[type-arg]
    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError):
        raise AuthenticationError("Token missing or invalid 'sub' claim.")

    app_meta = payload.get("app_metadata") or {}
    role = app_meta.get("role", "")
    email = payload.get("email", "")
    return CurrentUser(id=user_id, role=role, email=email)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=401, detail={"code": "AUTHENTICATION_REQUIRED", "message": "Missing Authorization header."})
    try:
        return _verify_token(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail={"code": exc.code, "message": exc.message})


def require_roles(*roles: str):  # type: ignore[return]
    """
    FastAPI dependency factory. Injects the current user and asserts their role.

    Usage:
        @router.get("/admin", dependencies=[Depends(require_roles("admin", "ops_agent"))])
    """
    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail={"code": "PERMISSION_DENIED", "message": f"Role '{user.role}' is not allowed here."},
            )
        return user
    return _check
