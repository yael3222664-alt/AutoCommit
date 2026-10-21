import hashlib
import logging
from datetime import datetime
from functools import lru_cache, partial
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db.session import get_db_session
from ..schemas import ExtractedRecord, RecordListResponse, RecordResponse, RecordStatus, RecordUpdateSchema
from ..services.document_pipeline import DocumentPipeline, ProcessedPage
from ..services.extractor_service import extract_record_from_image
from ..services.ingestion_service import (
    EmptyUploadError,
    FileTooLargeError,
    UnsupportedFileTypeError,
    validate_upload,
)
from ..services.page_image_store import PageImageError, PageImageStore
from ..services.pdf_service import PDFProcessingError
from ..services.record_service import (
    InvalidRecordUpdateError,
    InvalidTransitionError,
    RecordNotFoundError,
    RecordService,
)
from ..services.vision_provider import VisionProviderNotConfiguredError, get_vision_provider

logger = logging.getLogger(__name__)

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


@lru_cache
def get_page_image_store() -> PageImageStore:
    return PageImageStore(get_settings().PAGE_IMAGE_DIR)


def _store_page_images(images: PageImageStore, document_id: str, pages: list[ProcessedPage]) -> list[ExtractedRecord]:
    """Persist each page image and link it from its record. A storage failure only loses the image."""
    records = []
    for page in pages:
        try:
            key = images.save(document_id, page.record.page_number, page.image)
        except (OSError, PageImageError):
            logger.exception("Could not store image for page %d", page.record.page_number)
            key = None
        records.append(page.record.model_copy(update={"image_path": key}))
    return records


def get_record_service(session: Session = Depends(get_db_session)) -> RecordService:
    return RecordService(session)


async def _read_limited(upload: UploadFile, max_bytes: int) -> bytes:
    """Read at most max_bytes + 1 bytes so oversized uploads are detected without buffering them."""
    data = bytearray()
    while len(data) <= max_bytes:
        chunk = await upload.read(min(_READ_CHUNK_SIZE, max_bytes + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


@router.post("/documents/upload", response_model=list[RecordResponse])
async def upload_document(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
    records: RecordService = Depends(get_record_service),
    images: PageImageStore = Depends(get_page_image_store),
) -> list[RecordResponse]:
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
        pages = await pipeline.process_pages(data, file_type)
    except PDFProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc

    document_id = hashlib.sha256(data).hexdigest()
    extracted = await run_in_threadpool(_store_page_images, images, document_id, pages)
    try:
        record_ids = await run_in_threadpool(records.save_records, extracted, document_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not persist extracted records") from exc
    return await run_in_threadpool(records.get_records_by_ids, record_ids)


@router.get("/records", response_model=RecordListResponse)
def list_records(
    status_filter: Optional[RecordStatus] = Query(None, alias="status"),
    sender: Optional[str] = None,
    requires_manual_review: Optional[bool] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    records: RecordService = Depends(get_record_service),
) -> RecordListResponse:
    items, total = records.get_records(
        status=status_filter,
        sender=sender,
        requires_manual_review=requires_manual_review,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    return RecordListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/records/{record_id}", response_model=RecordResponse)
def get_record(record_id: str, records: RecordService = Depends(get_record_service)) -> RecordResponse:
    try:
        return records.get_record(record_id)
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found") from exc


@router.patch("/records/{record_id}", response_model=RecordResponse)
def update_record(
    record_id: str,
    patch: RecordUpdateSchema,
    records: RecordService = Depends(get_record_service),
) -> RecordResponse:
    try:
        return records.update_record(record_id, patch)
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found") from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except InvalidRecordUpdateError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/records/{record_id}/image", response_class=FileResponse)
def get_record_image(
    record_id: str,
    records: RecordService = Depends(get_record_service),
    images: PageImageStore = Depends(get_page_image_store),
) -> FileResponse:
    """The page image the record was extracted from; bounding boxes are normalized to it."""
    try:
        record = records.get_record(record_id)
    except RecordNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found") from exc
    stored = images.resolve(record.image_path) if record.image_path else None
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Page image not available")
    path, media_type = stored
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})
