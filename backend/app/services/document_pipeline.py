"""Orchestrates per-page processing of an uploaded document."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..schemas import ExtractedRecord, FieldValue
from .extractor_service import ExtractionError
from .ingestion_service import FileType
from .pdf_service import convert_pdf_to_images

logger = logging.getLogger(__name__)

EXTRACTION_FAILED_STATUS = "extraction_failed"

PageExtractor = Callable[[bytes, int], Awaitable[ExtractedRecord]]


def build_failed_record(page_number: int, reason: str) -> ExtractedRecord:
    """Record for a page that could not be processed: all fields empty, never guessed."""
    return ExtractedRecord(
        page_number=page_number,
        first_name=FieldValue(),
        last_name=FieldValue(),
        id_number=FieldValue(),
        received_date=FieldValue(),
        sender=FieldValue(),
        requires_manual_review=True,
        review_reasons=[reason],
        status=EXTRACTION_FAILED_STATUS,
    )


@dataclass(frozen=True)
class ProcessedPage:
    record: ExtractedRecord
    image: bytes


class DocumentPipeline:
    """Splits a document into pages and extracts each page independently.

    Vision calls are limited to `max_concurrency` across all documents handled by
    this instance. A failed page yields an `extraction_failed` record and does not
    affect its siblings.
    """

    def __init__(self, extractor: PageExtractor, max_concurrency: int = 3) -> None:
        self._extract = extractor
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def process(self, file_bytes: bytes, file_type: FileType) -> list[ExtractedRecord]:
        return [page.record for page in await self.process_pages(file_bytes, file_type)]

    async def process_pages(self, file_bytes: bytes, file_type: FileType) -> list[ProcessedPage]:
        """Like `process`, but also returns each page image the record was extracted from."""
        if file_type is FileType.pdf:
            images = await asyncio.to_thread(convert_pdf_to_images, file_bytes)
        else:
            images = [file_bytes]
        records = await asyncio.gather(
            *(self._process_page(image, number) for number, image in enumerate(images, start=1))
        )
        return [ProcessedPage(record=record, image=image) for record, image in zip(records, images)]

    async def _process_page(self, image_bytes: bytes, page_number: int) -> ExtractedRecord:
        async with self._semaphore:
            try:
                return await self._extract(image_bytes, page_number)
            except ExtractionError as exc:
                logger.warning("Extraction failed for page %d (%s)", page_number, exc.code)
                return build_failed_record(page_number, exc.code)
            except Exception:
                logger.exception("Unexpected error while processing page %d", page_number)
                return build_failed_record(page_number, "processing_error")
