"""
Lazy user provisioning middleware.

On every authenticated request, checks whether an identity.users row exists
for the JWT's sub claim. If not, creates a skeleton record from the JWT
claims (UUID, email, role). This eliminates any dependency on Supabase
user.created webhooks.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.auth import AuthenticationError, verify_token
from app.core.database import AsyncSessionFactory
from app.core.exceptions import UFirstError
from app.modules.identity.service import IdentityService

log = logging.getLogger(__name__)


class LazyUserProvisioningMiddleware(BaseHTTPMiddleware):
    """
    Ensures an identity.users row exists for every authenticated request.

    Invalid tokens (AuthenticationError) are passed through so the route
    handler's get_current_user dependency returns the correct 401. All other
    provisioning failures (unknown role, DB errors) are returned immediately
    so the request never reaches a route handler without a valid identity row.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ")
            try:
                current_user = verify_token(token)
            except AuthenticationError:
                pass  # Route handler will reject with 401
            else:
                try:
                    async with AsyncSessionFactory() as session:
                        async with session.begin():
                            db_user = await IdentityService(session).get_or_create_user(
                                current_user.id,
                                email=current_user.email,
                                role=current_user.role,
                            )
                    # Store DB user on request so get_current_user can use the
                    # authoritative DB role instead of whatever the JWT carries.
                    request.state.identity = db_user
                except UFirstError as exc:
                    return JSONResponse(
                        status_code=exc.http_status,
                        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                    )
                except Exception:
                    log.exception(
                        "Unexpected error during lazy provisioning for user %s",
                        current_user.id,
                    )
                    return JSONResponse(
                        status_code=500,
                        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred.", "details": {}}},
                    )

        return await call_next(request)
