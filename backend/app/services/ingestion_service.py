"""Upload validation: detects supported file types from content, not client metadata."""

from enum import Enum
from typing import Optional

from .pdf_service import has_pdf_header

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"


class FileType(str, Enum):
    pdf = "pdf"
    png = "png"
    jpeg = "jpeg"


IMAGE_MIME_TYPES = {FileType.png: "image/png", FileType.jpeg: "image/jpeg"}


class UploadValidationError(ValueError):
    """Base class for rejected uploads."""


class EmptyUploadError(UploadValidationError):
    pass


class FileTooLargeError(UploadValidationError):
    pass


class UnsupportedFileTypeError(UploadValidationError):
    pass


def detect_file_type(data: bytes) -> Optional[FileType]:
    if data.startswith(_PNG_SIGNATURE):
        return FileType.png
    if data.startswith(_JPEG_SIGNATURE):
        return FileType.jpeg
    if has_pdf_header(data):
        return FileType.pdf
    return None


def detect_image_mime_type(data: bytes) -> Optional[str]:
    file_type = detect_file_type(data)
    return IMAGE_MIME_TYPES.get(file_type) if file_type else None


def validate_upload(data: bytes, max_bytes: int) -> FileType:
    if not data:
        raise EmptyUploadError("File is empty")
    if len(data) > max_bytes:
        raise FileTooLargeError(f"File exceeds the maximum size of {max_bytes} bytes")
    file_type = detect_file_type(data)
    if file_type is None:
        raise UnsupportedFileTypeError("Unsupported file type; expected PDF, PNG, or JPEG")
    return file_type
