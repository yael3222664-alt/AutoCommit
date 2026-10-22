"""Builds the configured page image store."""

import logging

from ..config import Settings
from .page_image_store import LocalPageImageStore, PageImageStorageError, PageImageStore
from .supabase_image_store import PublicBucketError, SupabasePageImageStore

logger = logging.getLogger(__name__)


class StorageNotConfiguredError(ValueError):
    pass


def build_page_image_store(settings: Settings) -> PageImageStore:
    if settings.PAGE_IMAGE_STORAGE == "local":
        return LocalPageImageStore(settings.PAGE_IMAGE_DIR)
    key = settings.SUPABASE_SERVICE_ROLE_KEY.get_secret_value() if settings.SUPABASE_SERVICE_ROLE_KEY else ""
    if not settings.SUPABASE_URL or not key:
        raise StorageNotConfiguredError(
            "PAGE_IMAGE_STORAGE=supabase requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
        )
    try:
        return SupabasePageImageStore(
            url=settings.SUPABASE_URL,
            service_role_key=key,
            bucket=settings.SUPABASE_STORAGE_BUCKET,
            timeout_seconds=settings.STORAGE_TIMEOUT_SECONDS,
            max_retries=settings.STORAGE_MAX_RETRIES,
        )
    except ValueError as exc:
        raise StorageNotConfiguredError(str(exc)) from exc


def verify_page_image_store(store: PageImageStore) -> None:
    """Startup check. A public bucket is fatal; an unreachable backend only affects images."""
    try:
        store.check()
    except PublicBucketError:
        raise
    except PageImageStorageError as exc:
        logger.warning("Page image storage check failed at startup: %s", exc)
