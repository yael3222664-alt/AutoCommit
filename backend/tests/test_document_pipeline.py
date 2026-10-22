import asyncio

import pymupdf
import pytest

from backend.app.schemas import ExtractedRecord, FieldValue
from backend.app.services.document_pipeline import EXTRACTION_FAILED_STATUS, DocumentPipeline
from backend.app.services.ingestion_service import FileType
from backend.app.services.pdf_service import PdfPages, PDFProcessingError
from backend.tests.fakes import PNG_BYTES

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _pdf(page_count: int) -> bytes:
    with pymupdf.open() as document:
        for number in range(page_count):
            document.new_page(width=72, height=144).insert_text((10, 20), f"Page {number + 1}")
        return document.tobytes()


def _record(page_number: int) -> ExtractedRecord:
    empty = FieldValue()
    return ExtractedRecord(
        page_number=page_number, first_name=empty, last_name=empty, id_number=empty, received_date=empty, sender=empty
    )


def _run(coro):
    return asyncio.run(coro)


def test_pages_are_streamed_through_the_sink_one_slot_at_a_time():
    events: list[tuple[str, int]] = []

    async def extractor(image: bytes, page_number: int) -> ExtractedRecord:
        events.append(("extract", page_number))
        await asyncio.sleep(0)
        return _record(page_number)

    def sink(page_number: int, image: bytes) -> str:
        assert image.startswith(PNG_SIGNATURE)
        events.append(("store", page_number))
        return f"doc/page_{page_number}.png"

    records = _run(DocumentPipeline(extractor, max_concurrency=1).process(_pdf(3), FileType.pdf, image_sink=sink))

    assert events == [("extract", 1), ("store", 1), ("extract", 2), ("store", 2), ("extract", 3), ("store", 3)]
    assert [r.image_path for r in records] == [f"doc/page_{n}.png" for n in (1, 2, 3)]


def test_rendered_pages_in_flight_never_exceed_max_concurrency(monkeypatch):
    live = {"now": 0, "max": 0}
    real_render = PdfPages.render_png

    def counting_render(self, page_number):
        live["now"] += 1
        live["max"] = max(live["max"], live["now"])
        return real_render(self, page_number)

    def sink(page_number: int, image: bytes) -> str:
        live["now"] -= 1
        return "key"

    async def extractor(image: bytes, page_number: int) -> ExtractedRecord:
        await asyncio.sleep(0.01)
        return _record(page_number)

    monkeypatch.setattr(PdfPages, "render_png", counting_render)
    records = _run(DocumentPipeline(extractor, max_concurrency=2).process(_pdf(6), FileType.pdf, image_sink=sink))

    assert len(records) == 6
    assert live["max"] == 2


def test_page_that_fails_to_render_does_not_affect_siblings(monkeypatch):
    real_render = PdfPages.render_png
    stored: list[int] = []

    def flaky_render(self, page_number):
        if page_number == 2:
            raise PDFProcessingError("Failed to render PDF page")
        return real_render(self, page_number)

    async def extractor(image: bytes, page_number: int) -> ExtractedRecord:
        return _record(page_number)

    def sink(page_number: int, image: bytes) -> str:
        stored.append(page_number)
        return f"doc/page_{page_number}.png"

    monkeypatch.setattr(PdfPages, "render_png", flaky_render)
    records = _run(DocumentPipeline(extractor).process(_pdf(3), FileType.pdf, image_sink=sink))

    assert [r.page_number for r in records] == [1, 2, 3]
    assert records[1].status == EXTRACTION_FAILED_STATUS
    assert records[1].review_reasons == ["render_failed"]
    assert records[1].image_path is None
    assert records[0].status != EXTRACTION_FAILED_STATUS and records[2].status != EXTRACTION_FAILED_STATUS
    assert sorted(stored) == [1, 3]


def test_sink_returning_none_leaves_image_path_empty():
    async def extractor(image: bytes, page_number: int) -> ExtractedRecord:
        return _record(page_number)

    records = _run(DocumentPipeline(extractor).process(_pdf(2), FileType.pdf, image_sink=lambda n, b: None))
    assert [r.image_path for r in records] == [None, None]


def test_image_upload_passes_original_bytes_to_extractor_and_sink():
    seen: list[bytes] = []

    async def extractor(image: bytes, page_number: int) -> ExtractedRecord:
        seen.append(image)
        return _record(page_number)

    def sink(page_number: int, image: bytes) -> str:
        seen.append(image)
        return "doc/page_1.png"

    records = _run(DocumentPipeline(extractor).process(PNG_BYTES, FileType.png, image_sink=sink))
    assert seen == [PNG_BYTES, PNG_BYTES]
    assert [(r.page_number, r.image_path) for r in records] == [(1, "doc/page_1.png")]


def test_unopenable_pdf_raises_before_any_page_is_processed():
    async def extractor(image: bytes, page_number: int) -> ExtractedRecord:
        raise AssertionError("must not be called")

    with pytest.raises(PDFProcessingError):
        _run(DocumentPipeline(extractor).process(b"%PDF-1.7\n" + b"\x00garbage" * 50, FileType.pdf))
