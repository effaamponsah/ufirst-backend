from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.auth import warm_jwks_cache
from app.core.database import engine
from app.core.middleware import IdempotencyMiddleware, register_exception_handlers
from app.core.redis import close_redis

log = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def _check_db() -> None:
    async with engine.connect():
        pass


async def _check_redis() -> None:
    from app.core.redis import get_redis

    await get_redis().ping()


async def _wait_for_services(retries: int = 5, delay: float = 2.0) -> None:
    """
    Probe DB and Redis before accepting traffic. Retries with a fixed delay to
    handle the race between the app starting and docker-compose healthchecks.
    Raises RuntimeError after all retries are exhausted so the process exits
    with a non-zero code instead of silently serving errors.
    """
    import asyncio

    checks = {"db": _check_db, "redis": _check_redis}

    for name, probe in checks.items():
        for attempt in range(1, retries + 1):
            try:
                await probe()
                log.info("  ✓ %s", name)
                break
            except Exception as exc:
                if attempt == retries:
                    raise RuntimeError(
                        f"Startup aborted: {name} not reachable after {retries} attempts — {exc}"
                    )
                log.warning("  ✗ %s unavailable (attempt %d/%d), retrying in %.0fs…", name, attempt, retries, delay)
                await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ── Startup ──────────────────────────────────────────────────────────
    log.info("Starting %s (dev_mode=%s)", settings.app_name, settings.dev_mode)
    log.info("Checking downstream services…")
    await _wait_for_services()
    await warm_jwks_cache()
    log.info("All checks passed — ready to serve.")
    yield
    # ── Shutdown ─────────────────────────────────────────────────────────
    await close_redis()
    await engine.dispose()
    log.info("Shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(IdempotencyMiddleware)
    from app.modules.identity.middleware import LazyUserProvisioningMiddleware
    app.add_middleware(LazyUserProvisioningMiddleware)

    # ── Exception handlers ────────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ───────────────────────────────────────────────────────────
    from app.modules.identity.routes import router as identity_router
    from app.modules.wallet.routes import router as wallet_router
    from app.modules.wallet.openbanking.webhooks import webhook_router
    from app.modules.card.routes import router as card_router
    app.include_router(identity_router, prefix="/api/v1")
    app.include_router(wallet_router, prefix="/api/v1")
    app.include_router(webhook_router, prefix="/api/v1")
    app.include_router(card_router, prefix="/api/v1")

    # ── Health check ──────────────────────────────────────────────────────
    @app.get("/health", tags=["ops"])
    async def health() -> dict:  # type: ignore[type-arg]
        from sqlalchemy import text
        from app.core.database import AsyncSessionFactory
        from app.core.redis import get_redis

        db_ok = False
        redis_ok = False

        try:
            async with AsyncSessionFactory() as session:
                await session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            log.exception("DB health check failed")

        try:
            await get_redis().ping()
            redis_ok = True
        except Exception:
            log.exception("Redis health check failed")

        status = "ok" if (db_ok and redis_ok) else "degraded"
        return {
            "status": status,
            "db": "ok" if db_ok else "error",
            "redis": "ok" if redis_ok else "error",
        }

    return app


app = create_app()
