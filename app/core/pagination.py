from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class PaginationMeta(BaseModel):
    total: int
    page: int
    per_page: int

    @property
    def total_pages(self) -> int:
        if self.per_page == 0:
            return 0
        return (self.total + self.per_page - 1) // self.per_page


class PaginatedResponse(BaseModel, Generic[T]):
    data: list[T]
    meta: PaginationMeta


async def paginate(
    session: AsyncSession,
    query: Select,  # type: ignore[type-arg]
    page: int,
    per_page: int,
) -> tuple[list, int]:
    """
    Execute a paginated query.

    Returns (rows, total_count). The caller is responsible for mapping rows to
    the appropriate response schema.

    Usage:
        rows, total = await paginate(session, select(MyModel).where(...), page=1, per_page=20)
        return PaginatedResponse(
            data=[MySchema.model_validate(r) for r in rows],
            meta=PaginationMeta(total=total, page=page, per_page=per_page),
        )
    """
    # Count total matching rows without pagination
    count_query = select(func.count()).select_from(query.subquery())
    total: int = (await session.execute(count_query)).scalar_one()

    # Apply offset + limit
    offset = (page - 1) * per_page
    paginated = query.offset(offset).limit(per_page)
    rows = list((await session.execute(paginated)).scalars().all())

    return rows, total
