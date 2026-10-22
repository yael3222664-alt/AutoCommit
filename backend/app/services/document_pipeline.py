"""Orchestrates per-page processing of an uploaded document."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Optional

from ..schemas import ExtractedRecord, FieldValue
from .extractor_service import ExtractionError
from .ingestion_service import FileType
from .pdf_service import PDFProcessingError, open_pdf

logger = logging.getLogger(__name__)

EXTRACTION_FAILED_STATUS = "extraction_failed"
RENDER_FAILED_REASON = "render_failed"

PageExtractor = Callable[[bytes, int], Awaitable[ExtractedRecord]]
PageLoader = Callable[[int], Awaitable[bytes]]
# Persists a page image and returns its storage key, or None if it could not be stored.
PageImageSink = Callable[[int, bytes], Optional[str]]


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


class DocumentPipeline:
    """Splits a document into pages and processes each page independently.

    At most `max_concurrency` pages are in flight across all documents handled by this
    instance. A page is rendered only when it gets a slot and its image is handed to the
    optional image sink before the slot is released, so memory stays bounded for long or
    large documents. A failed page yields an `extraction_failed` record and does not
    affect its siblings.
    """

    def __init__(self, extractor: PageExtractor, max_concurrency: int = 3) -> None:
        self._extract = extractor
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def process(
        self, file_bytes: bytes, file_type: FileType, image_sink: Optional[PageImageSink] = None
    ) -> list[ExtractedRecord]:
        """Extract one record per page, in page order.

        When `image_sink` is given, each page image is passed to it and the returned
        storage key is set as the record's `image_path`.
        """
        if file_type is not FileType.pdf:

            async def original_image(_page_number: int) -> bytes:
                return file_bytes

            return await self._process_all(1, original_image, image_sink)

        pages = await asyncio.to_thread(open_pdf, file_bytes)
        with pages:

            async def rendered_page(page_number: int) -> bytes:
                return await asyncio.to_thread(pages.render_png, page_number)

            return await self._process_all(pages.page_count, rendered_page, image_sink)

    async def _process_all(
        self, page_count: int, load_image: PageLoader, image_sink: Optional[PageImageSink]
    ) -> list[ExtractedRecord]:
        return list(
            await asyncio.gather(
                *(self._process_page(number, load_image, image_sink) for number in range(1, page_count + 1))
            )
        )

    async def _process_page(
        self, page_number: int, load_image: PageLoader, image_sink: Optional[PageImageSink]
    ) -> ExtractedRecord:
        async with self._semaphore:
            try:
                image = await load_image(page_number)
            except PDFProcessingError:
                logger.warning("Rendering failed for page %d", page_number)
                return build_failed_record(page_number, RENDER_FAILED_REASON)
            record = await self._extract_page(image, page_number)
            if image_sink is not None:
                key = await asyncio.to_thread(image_sink, page_number, image)
                record = record.model_copy(update={"image_path": key})
            return record

    async def _extract_page(self, image_bytes: bytes, page_number: int) -> ExtractedRecord:
        try:
            return await self._extract(image_bytes, page_number)
        except ExtractionError as exc:
            logger.warning("Extraction failed for page %d (%s)", page_number, exc.code)
            return build_failed_record(page_number, exc.code)
        except Exception:
            logger.exception("Unexpected error while processing page %d", page_number)
            return build_failed_record(page_number, "processing_error")
