"""Filesystem storage of rendered page images, keyed by document and page."""

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from .ingestion_service import detect_image_mime_type

_DOCUMENT_ID = re.compile(r"^[0-9a-f]{64}$")
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}
_MIME_TYPES = {extension: mime for mime, extension in _EXTENSIONS.items()}


class PageImageError(ValueError):
    pass


class PageImageStore:
    """Stores one image per (document_id, page_number) under `root`.

    Keys are relative paths built only from a SHA-256 document id and a page number, and
    every lookup is confined to `root`, so stored keys cannot be used for path traversal.
    Writes are atomic (temporary file + rename) and idempotent for the same document.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    def save(self, document_id: str, page_number: int, image_bytes: bytes) -> str:
        if not _DOCUMENT_ID.fullmatch(document_id):
            raise PageImageError("document_id must be a lowercase SHA-256 hex digest")
        if page_number < 1:
            raise PageImageError("page_number must be >= 1")
        mime_type = detect_image_mime_type(image_bytes)
        if mime_type is None:
            raise PageImageError("Page image must be PNG or JPEG")

        key = f"{document_id}/{page_number}.{_EXTENSIONS[mime_type]}"
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
        """Return (path, mime type) of a stored image, or None if missing or outside the store."""
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root) or not path.is_file():
            return None
        mime_type = _MIME_TYPES.get(path.suffix.lstrip("."))
        if mime_type is None:
            return None
        return path, mime_type
