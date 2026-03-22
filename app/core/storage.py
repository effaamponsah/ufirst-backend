from __future__ import annotations

import mimetypes

import httpx

from app.config import settings


class SupabaseStorageClient:
    """Thin async client for Supabase Storage.

    Uses the Supabase Storage REST API directly so no additional SDK dependency
    is required. Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to be set.
    """

    def __init__(self) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to use Supabase Storage."
            )
        self._base = settings.supabase_url.rstrip("/") + "/storage/v1"
        self._headers = {
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "apikey": settings.supabase_service_role_key,
        }

    async def upload(self, bucket: str, path: str, data: bytes, filename: str) -> str:
        """Upload *data* to *bucket*/*path* and return the public URL.

        Raises httpx.HTTPStatusError on non-2xx responses.
        """
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "application/octet-stream"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base}/object/{bucket}/{path}",
                content=data,
                headers={
                    **self._headers,
                    "Content-Type": content_type,
                    "x-upsert": "true",
                },
            )
            resp.raise_for_status()

        return f"{self._base}/object/public/{bucket}/{path}"
