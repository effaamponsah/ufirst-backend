"""
In-process typed event bus.

Modules publish events after completing state changes. Other modules subscribe
to those events to perform side-effects (notifications, compliance checks, etc.)
without creating direct import dependencies.

Rules:
- Handlers must not raise — log and swallow exceptions to avoid affecting the
  caller's transaction.
- Handlers are called synchronously in publication order. For heavy work,
  dispatch to Celery from inside the handler.
- Events are Pydantic models so they are always typed and validated.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Type, TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

E = TypeVar("E", bound=BaseModel)

# Handler type: accepts an event, returns a coroutine
AsyncHandler = Callable[[Any], Coroutine[Any, Any, None]]

_subscribers: dict[type, list[AsyncHandler]] = defaultdict(list)


def subscribe(event_type: Type[E], handler: AsyncHandler) -> None:
    """Register an async handler for the given event type."""
    _subscribers[event_type].append(handler)


async def publish(event: BaseModel) -> None:
    """
    Dispatch an event to all registered handlers.

    Safe to call from within a database transaction — handlers are invoked
    after the call returns, using the running event loop.
    """
    handlers = _subscribers.get(type(event), [])
    for handler in handlers:
        try:
            await handler(event)
        except Exception:
            log.exception(
                "Event handler %s failed for event %s",
                handler.__qualname__,
                type(event).__name__,
            )


def publish_sync(event: BaseModel) -> None:
    """
    Synchronous wrapper for publishing from non-async contexts (e.g. Celery tasks).
    Creates a new event loop if none is running.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(publish(event))
    except RuntimeError:
        asyncio.run(publish(event))
