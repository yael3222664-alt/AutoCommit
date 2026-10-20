import asyncio

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.config import Settings, get_settings
from backend.app.main import app
from backend.app.schemas import ExtractedRecord, FieldValue
from backend.app.services.document_pipeline import DocumentPipeline
from backend.app.services.extractor_service import ExtractionError
from backend.tests.fakes import JPEG_BYTES, PNG_BYTES

UPLOAD_URL = "/api/documents/upload"


def _pdf(page_count: int) -> bytes:
    with pymupdf.open() as document:
        for number in range(page_count):
            document.new_page(width=200, height=200).insert_text((20, 20), f"Page {number + 1}")
        return document.tobytes()


def _record(page_number: int) -> ExtractedRecord:
    return ExtractedRecord(
        page_number=page_number,
        first_name=FieldValue(value=f"name-{page_number}", confidence="high"),
        last_name=FieldValue(),
        id_number=FieldValue(),
        received_date=FieldValue(),
        sender=FieldValue(),
    )


class RecordingExtractor:
    def __init__(self, fail_pages=(), crash_pages=(), delay=0.0):
        self.fail_pages = set(fail_pages)
        self.crash_pages = set(crash_pages)
        self.delay = delay
        self.calls: list[tuple[bytes, int]] = []
        self.active = 0
        self.max_active = 0

    async def __call__(self, image_bytes: bytes, page_number: int) -> ExtractedRecord:
        self.calls.append((image_bytes, page_number))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if page_number in self.fail_pages:
                raise ExtractionError("malformed response")
            if page_number in self.crash_pages:
                raise RuntimeError("bug")
            return _record(page_number)
        finally:
            self.active -= 1


@pytest.fixture
def client_with(request):
    def make(extractor=None, settings=None, max_concurrency=3):
        extractor = extractor or RecordingExtractor()
        app.dependency_overrides[routes.get_document_pipeline] = lambda: DocumentPipeline(extractor, max_concurrency)
        if settings is not None:
            app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app), extractor

    yield make
    app.dependency_overrides.clear()


def _upload(client, content, filename="doc.pdf", content_type="application/pdf"):
    return client.post(UPLOAD_URL, files={"file": (filename, content, content_type)})


def test_pdf_pages_become_independent_records(client_with):
    client, extractor = client_with()
    response = _upload(client, _pdf(3))

    assert response.status_code == 200
    body = response.json()
    assert [r["page_number"] for r in body] == [1, 2, 3]
    assert [r["first_name"]["value"] for r in body] == ["name-1", "name-2", "name-3"]
    assert sorted(page for _, page in extractor.calls) == [1, 2, 3]
    assert all(image.startswith(b"\x89PNG") for image, _ in extractor.calls)


@pytest.mark.parametrize("content, content_type", [(PNG_BYTES, "image/png"), (JPEG_BYTES, "image/jpeg")])
def test_image_upload_is_a_single_page(client_with, content, content_type):
    client, extractor = client_with()
    response = _upload(client, content, "scan", content_type)

    assert response.status_code == 200
    assert [r["page_number"] for r in response.json()] == [1]
    assert extractor.calls == [(content, 1)]


def test_failed_page_does_not_affect_siblings(client_with):
    client, _ = client_with(RecordingExtractor(fail_pages={2}, crash_pages={3}))
    body = _upload(client, _pdf(4)).json()

    assert [r["status"] for r in body] == ["pending_review", "extraction_failed", "extraction_failed", "pending_review"]
    failed = body[1]
    assert failed["requires_manual_review"] is True
    assert failed["review_reasons"] == ["extraction_failed"]
    assert failed["first_name"]["value"] is None
    assert body[2]["review_reasons"] == ["processing_error"]
    assert body[0]["first_name"]["value"] == "name-1"


def test_concurrent_vision_calls_are_limited(client_with):
    extractor = RecordingExtractor(delay=0.02)
    client, _ = client_with(extractor, max_concurrency=3)

    assert _upload(client, _pdf(7)).status_code == 200
    assert len(extractor.calls) == 7
    assert extractor.max_active == 3


def test_empty_upload_is_rejected(client_with):
    client, extractor = client_with()
    assert _upload(client, b"").status_code == 400
    assert extractor.calls == []


def test_unsupported_file_type_is_rejected_regardless_of_declared_type(client_with):
    client, _ = client_with()
    response = _upload(client, b"GIF89a fake", "evil.pdf", "application/pdf")
    assert response.status_code == 415


def test_oversized_upload_is_rejected(client_with):
    client, extractor = client_with(settings=Settings(_env_file=None, MAX_UPLOAD_SIZE_MB=1))
    response = _upload(client, PNG_BYTES + b"\x00" * (1024 * 1024))

    assert response.status_code == 413
    assert extractor.calls == []


def test_corrupted_pdf_is_rejected(client_with):
    client, _ = client_with()
    response = _upload(client, b"%PDF-1.7\n" + b"\x00garbage" * 50)
    assert response.status_code == 422


def test_missing_vision_configuration_returns_503(monkeypatch):
    monkeypatch.setattr(routes, "get_settings", lambda: Settings(_env_file=None, GEMINI_API_KEY=None))
    routes._build_document_pipeline.cache_clear()
    try:
        response = _upload(TestClient(app), _pdf(1))
    finally:
        routes._build_document_pipeline.cache_clear()
    assert response.status_code == 503
