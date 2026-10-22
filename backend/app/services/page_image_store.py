"""Storage of rendered page images, keyed by document and page.

Object keys are built only from a SHA-256 document id and a page number
(`<document_id>/page_<n>.<ext>`), so they are deterministic per page, never collide across
documents, and cannot be used for path traversal. Records store the key, never a URL.
"""

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .ingestion_service import detect_image_mime_type

_DOCUMENT_ID = re.compile(r"^[0-9a-f]{64}$")
# Current keys, and keys written before the `page_` prefix (`<document_id>/<n>.<ext>`).
_KEY = re.compile(r"^[0-9a-f]{64}/(?:page_)?[1-9][0-9]{0,5}\.(?P<extension>png|jpg)$")
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}
_MIME_TYPES = {extension: mime for mime, extension in _EXTENSIONS.items()}


class PageImageError(Exception):
    """Base class for page image storage errors."""


class InvalidPageImageError(PageImageError):
    """The image, document id or page number is invalid; nothing was stored."""


class PageImageStorageError(PageImageError):
    """The storage backend failed or could not be reached."""


@dataclass(frozen=True)
class StoredImage:
    content: bytes
    media_type: str


class PageImageStore(Protocol):
    def save(self, document_id: str, page_number: int, image_bytes: bytes) -> str:
        """Store the image of one page (idempotent per document and page) and return its key."""
        ...

    def load(self, key: str) -> Optional[StoredImage]:
        """Return a stored image, or None if the key is invalid or the image does not exist."""
        ...

    def check(self) -> None:
        """Verify the backend is usable; raise PageImageError if not."""
        ...


def prepare_page_image(document_id: str, page_number: int, image_bytes: bytes) -> tuple[str, str]:
    """Validate a page image and return its (key, media type)."""
    if not _DOCUMENT_ID.fullmatch(document_id):
        raise InvalidPageImageError("document_id must be a lowercase SHA-256 hex digest")
    if page_number < 1:
        raise InvalidPageImageError("page_number must be >= 1")
    mime_type = detect_image_mime_type(image_bytes)
    if mime_type is None:
        raise InvalidPageImageError("Page image must be PNG or JPEG")
    return f"{document_id}/page_{page_number}.{_EXTENSIONS[mime_type]}", mime_type


def media_type_for_key(key: str) -> Optional[str]:
    """The media type of a well-formed key, or None for anything else."""
    match = _KEY.fullmatch(key)
    return _MIME_TYPES[match.group("extension")] if match else None


class LocalPageImageStore:
    """Stores page images under a local directory.

    Every lookup is confined to `root`, so neither malformed keys nor symlinks can read
    files outside it. Writes are atomic (temporary file + rename) and idempotent.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    def save(self, document_id: str, page_number: int, image_bytes: bytes) -> str:
        key, _ = prepare_page_image(document_id, page_number, image_bytes)
        destination = self._root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=destination.parent, prefix=".page-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(image_bytes)
            os.replace(temp_name, destination)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return key

    def resolve(self, key: str) -> Optional[tuple[Path, str]]:
        """Return (path, media type) of a stored image, or None if missing or outside the store."""
        media_type = media_type_for_key(key)
        if media_type is None:
            return None
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root) or not path.is_file():
            return None
        return path, media_type

    def load(self, key: str) -> Optional[StoredImage]:
        resolved = self.resolve(key)
        if resolved is None:
            return None
        path, media_type = resolved
        try:
            return StoredImage(content=path.read_bytes(), media_type=media_type)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PageImageStorageError(f"Could not read page image ({type(exc).__name__})") from exc

    def check(self) -> None:
        return None
