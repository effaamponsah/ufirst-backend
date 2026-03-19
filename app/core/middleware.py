"""
Application-level middleware:
  1. IdempotencyMiddleware  — deduplicates POST/PUT/PATCH using Idempotency-Key header
  2. register_exception_handlers — maps domain exceptions to the standard error envelope
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from app.core.exceptions import UFirstError
from app.core.redis import get_redis

log = logging.getLogger(__name__)

_IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH"}
_IDEMPOTENCY_TTL = 86_400  # 24 hours


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

        redis = get_redis()
        cache_key = f"idempotency:{key}"

        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            return Response(
                content=data["body"],
                status_code=data["status_code"],
                headers={"Content-Type": data.get("content_type", "application/json")},
            )

        # Process the request
        response = await call_next(request)

        # Cache only successful responses
        if 200 <= response.status_code < 300:
            body_chunks: list[bytes] = []
            async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                body_chunks.append(chunk)
            body = b"".join(body_chunks)

            await redis.setex(
                cache_key,
                _IDEMPOTENCY_TTL,
                json.dumps({
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type", "application/json"),
                    "body": body.decode(),
                }),
            )

            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
            )

        return response


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
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred.", "details": {}}},
        )
