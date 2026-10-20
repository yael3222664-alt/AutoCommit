from functools import lru_cache, partial

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from ..config import Settings, get_settings
from ..schemas import ExtractedRecord
from ..services.document_pipeline import DocumentPipeline
from ..services.extractor_service import extract_record_from_image
from ..services.ingestion_service import (
    EmptyUploadError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    validate_upload,
)
from ..services.pdf_service import PDFProcessingError
from ..services.vision_provider import VisionProviderNotConfiguredError, get_vision_provider

router = APIRouter()

_READ_CHUNK_SIZE = 1024 * 1024


@lru_cache
def _build_document_pipeline() -> DocumentPipeline:
    settings = get_settings()
    provider = get_vision_provider(settings)
    return DocumentPipeline(
        extractor=partial(extract_record_from_image, provider=provider),
        max_concurrency=settings.VISION_MAX_CONCURRENCY,
    )


def get_document_pipeline() -> DocumentPipeline:
    try:
        return _build_document_pipeline()
    except VisionProviderNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Vision provider is not configured") from exc


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Read at most max_bytes + 1 bytes so oversized uploads are detected without buffering them."""
    data = bytearray()
    while len(data) <= max_bytes:
        chunk = await upload.read(min(_READ_CHUNK_SIZE, max_bytes + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


@router.post("/documents/upload", response_model=list[ExtractedRecord])
async def upload_document(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> list[ExtractedRecord]:
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    data = await _read_limited(file, max_bytes)
    try:
        file_type = validate_upload(data, max_bytes)
    except EmptyUploadError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    try:
        return await pipeline.process(data, file_type)
    except PDFProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc
