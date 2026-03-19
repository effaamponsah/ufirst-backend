from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings

# Module-level singleton — initialised once and reused across requests.
_client: aioredis.Redis | None = None  # type: ignore[type-arg]


def get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    global _client
    if _client is None:
        _client = aioredis.from_url(
            str(settings.redis_url),
            decode_responses=True,
            encoding="utf-8",
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
