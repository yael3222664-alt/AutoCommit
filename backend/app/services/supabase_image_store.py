"""Page image storage in a private Supabase Storage bucket, accessed server-side only.

The service-role key is sent only to the Supabase Storage API from the backend. Images
are always read through authenticated requests and served by the API itself; no public or
signed bucket URL is ever produced, and the bucket must be private.
"""

import re
import time
from collections.abc import Callable
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from .ingestion_service import detect_image_mime_type
from .page_image_store import (
    PageImageStorageError,
    StoredImage,
    media_type_for_key,
    prepare_page_image,
)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY_SECONDS = 10.0
_BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class PublicBucketError(PageImageStorageError):
    """The configured bucket is public; medical page images must never be stored in it."""


class SupabasePageImageStore:
    def __init__(
        self,
        *,
        url: str,
        service_role_key: str,
        bucket: str,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        retry_base_delay_seconds: float = 0.5,
        client: Optional[httpx.Client] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
            raise ValueError("SUPABASE_URL must use https")
        if not service_role_key:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is empty")
        if not _BUCKET_NAME.fullmatch(bucket):
            raise ValueError("SUPABASE_STORAGE_BUCKET is not a valid bucket name")
        self._base_url = f"{url.rstrip('/')}/storage/v1"
        self._bucket = bucket
        self._headers = {"apikey": service_role_key}
        if service_role_key.startswith("eyJ"):
            # Legacy JWT service_role keys must also be sent as a bearer token.
            self._headers["authorization"] = f"Bearer {service_role_key}"
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay_seconds
        self._sleep = sleep
        self._client = client if client is not None else httpx.Client(timeout=timeout_seconds)

    def __repr__(self) -> str:
        return f"SupabasePageImageStore(base_url={self._base_url!r}, bucket={self._bucket!r})"

    def save(self, document_id: str, page_number: int, image_bytes: bytes) -> str:
        """Upload with upsert: retrying or re-uploading the same page rewrites only its own key."""
        key, media_type = prepare_page_image(document_id, page_number, image_bytes)
        response = self._request(
            "POST",
            f"/object/{self._bucket}/{key}",
            content=image_bytes,
            headers={"content-type": media_type, "x-upsert": "true", "cache-control": "no-store"},
        )
        if response.status_code != 200:
            raise PageImageStorageError(f"Page image upload failed with HTTP {response.status_code}")
        return key

    def load(self, key: str) -> Optional[StoredImage]:
        media_type = media_type_for_key(key)
        if media_type is None:
            return None
        response = self._request("GET", f"/object/authenticated/{self._bucket}/{key}")
        if _is_not_found(response):
            return None
        if response.status_code != 200:
            raise PageImageStorageError(f"Page image download failed with HTTP {response.status_code}")
        if detect_image_mime_type(response.content) != media_type:
            raise PageImageStorageError("Stored object is not the expected image type")
        return StoredImage(content=response.content, media_type=media_type)

    def check(self) -> None:
        """Verify the bucket exists, the key is accepted, and the bucket is private."""
        response = self._request("GET", f"/bucket/{self._bucket}")
        if _is_not_found(response):
            raise PageImageStorageError("Storage bucket does not exist; create it as a private bucket")
        if response.status_code in (401, 403):
            raise PageImageStorageError(f"Storage credentials were rejected (HTTP {response.status_code})")
        if response.status_code != 200:
            raise PageImageStorageError(f"Storage bucket check failed with HTTP {response.status_code}")
        try:
            public = response.json().get("public")
        except (ValueError, AttributeError) as exc:
            raise PageImageStorageError("Storage bucket check returned an unexpected response") from exc
        if public is not False:
            raise PublicBucketError("Storage bucket is public; page images require a private bucket")

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, f"{self._base_url}{path}", headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                if attempt == self._max_retries:
                    raise PageImageStorageError(f"Page image storage is unreachable ({type(exc).__name__})") from None
            else:
                if response.status_code not in RETRYABLE_STATUS_CODES or attempt == self._max_retries:
                    return response
            self._sleep(min(self._retry_base_delay * 2**attempt, _MAX_RETRY_DELAY_SECONDS))
        raise AssertionError("unreachable")


def _is_not_found(response: httpx.Response) -> bool:
    """Supabase Storage reports missing objects/buckets as 404, or as 400 with a 404 body."""
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and (
        str(body.get("statusCode")) == "404" or str(body.get("error", "")).lower() in ("not_found", "not found")
    )
