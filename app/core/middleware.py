"""
Application-level middleware:
  1. IdempotencyMiddleware  — deduplicates POST/PUT/PATCH using Idempotency-Key header
  2. register_exception_handlers — maps domain exceptions to the standard error envelope
"""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from app.core.auth import AuthenticationError, verify_token
from app.core.exceptions import DuplicateIdempotencyKey, IdempotencyConflict, UFirstError
from app.core.redis import get_redis

log = logging.getLogger(__name__)

_IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH"}
_IDEMPOTENCY_TTL = 86_400  # 24 hours
_STATE_IN_PROGRESS = "in_progress"
_STATE_COMPLETED = "completed"


def _error_response(exc: UFirstError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


def _request_actor_scope(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return "anonymous"

    token = auth_header.removeprefix("Bearer ")
    try:
        return str(verify_token(token).id)
    except AuthenticationError:
        # Scope invalid/opaque tokens independently so two bad tokens do not collide.
        return "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cache_key(request: Request, idempotency_key: str, actor_scope: str) -> str:
    scope = "\n".join(
        [
            request.method,
            request.url.path,
            request.url.query,
            actor_scope,
            idempotency_key,
        ]
    )
    return "idempotency:v2:" + hashlib.sha256(scope.encode("utf-8")).hexdigest()


def _fingerprint(request: Request, body: bytes) -> str:
    content_type = request.headers.get("content-type", "")
    digest = hashlib.sha256()
    digest.update(content_type.encode("utf-8"))
    digest.update(b"\n")
    digest.update(body)
    return digest.hexdigest()


def _restore_request_body(request: Request, body: bytes) -> None:
    async def receive() -> dict[str, bytes | bool | str]:
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    Cache responses for idempotent mutations.

    If an Idempotency-Key header is present and we have a cached result in
    Redis, return the cached response immediately without hitting the handler.

    On the first request, after the handler runs, we cache:
        {status_code, headers (content-type only), body}

    Only 2xx responses are cached. Errors are never cached — the client should
    be able to retry after fixing the problem.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        if request.method not in _IDEMPOTENT_METHODS:
            return await call_next(request)

        key = request.headers.get("Idempotency-Key")
        if not key:
            return await call_next(request)

        body = await request.body()
        _restore_request_body(request, body)

        redis = get_redis()
        actor_scope = _request_actor_scope(request)
        cache_key = _cache_key(request, key, actor_scope)
        fingerprint = _fingerprint(request, body)

        reservation = json.dumps(
            {"state": _STATE_IN_PROGRESS, "fingerprint": fingerprint}
        )
        reserved = await redis.set(
            cache_key, reservation, ex=_IDEMPOTENCY_TTL, nx=True
        )

        if not reserved:
            cached = await redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                if data.get("fingerprint") != fingerprint:
                    return _error_response(
                        IdempotencyConflict(
                            "Idempotency-Key reused with different request payload.",
                            details={
                                "method": request.method,
                                "path": request.url.path,
                            },
                        )
                    )
                if data.get("state") == _STATE_COMPLETED:
                    return Response(
                        content=data["body"],
                        status_code=data["status_code"],
                        headers={
                            "Content-Type": data.get(
                                "content_type", "application/json"
                            )
                        },
                    )
                return _error_response(
                    DuplicateIdempotencyKey(
                        "Another request with this Idempotency-Key is already in progress.",
                        details={
                            "method": request.method,
                            "path": request.url.path,
                        },
                    )
                )

        try:
            response = await call_next(request)
        except Exception:
            await redis.delete(cache_key)
            raise

        if not 200 <= response.status_code < 300:
            await redis.delete(cache_key)
            return response

        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body_chunks.append(chunk)
        response_body = b"".join(body_chunks)

        await redis.set(
            cache_key,
            json.dumps(
                {
                    "state": _STATE_COMPLETED,
                    "fingerprint": fingerprint,
                    "status_code": response.status_code,
                    "content_type": response.headers.get(
                        "content-type", "application/json"
                    ),
                    "body": response_body.decode(),
                }
            ),
            ex=_IDEMPOTENCY_TTL,
        )

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
        )


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(UFirstError)
    async def ufirst_error_handler(request: Request, exc: UFirstError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred.",
                    "details": {},
                }
            },
        )
