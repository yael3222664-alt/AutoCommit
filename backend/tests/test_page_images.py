import hashlib

import pymupdf
import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.main import app
from backend.app.schemas import ExtractedRecord, FieldValue
from backend.app.services.document_pipeline import DocumentPipeline
from backend.app.services.page_image_store import PageImageError, PageImageStore
from backend.app.services.record_service import RecordService
from backend.tests.fakes import JPEG_BYTES, PNG_BYTES

DOC_ID = "a" * 64


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


async def _extractor(_image: bytes, page_number: int) -> ExtractedRecord:
    return _record(page_number)


@pytest.fixture
def client():
    app.dependency_overrides[routes.get_document_pipeline] = lambda: DocumentPipeline(_extractor, 3)
    yield TestClient(app)
    app.dependency_overrides.pop(routes.get_document_pipeline, None)


def test_store_saves_png_and_jpeg_under_document_key(page_image_store):
    png_key = page_image_store.save(DOC_ID, 1, PNG_BYTES)
    jpeg_key = page_image_store.save(DOC_ID, 2, JPEG_BYTES)
    assert png_key == f"{DOC_ID}/1.png"
    assert jpeg_key == f"{DOC_ID}/2.jpg"
    path, mime = page_image_store.resolve(png_key)
    assert path.read_bytes() == PNG_BYTES and mime == "image/png"
    assert page_image_store.resolve(jpeg_key)[1] == "image/jpeg"


def test_store_overwrites_idempotently_and_leaves_no_temp_files(page_image_store, tmp_path):
    page_image_store.save(DOC_ID, 1, PNG_BYTES)
    page_image_store.save(DOC_ID, 1, PNG_BYTES)
    assert sorted(p.name for p in (tmp_path / "pages" / DOC_ID).iterdir()) == ["1.png"]


@pytest.mark.parametrize(
    "document_id, page_number, data",
    [("../etc", 1, PNG_BYTES), ("A" * 64, 1, PNG_BYTES), (DOC_ID, 0, PNG_BYTES), (DOC_ID, 1, b"%PDF-1.7")],
)
def test_store_rejects_invalid_input(page_image_store, document_id, page_number, data):
    with pytest.raises(PageImageError):
        page_image_store.save(document_id, page_number, data)


def test_resolve_refuses_keys_outside_the_store(tmp_path):
    (tmp_path / "secret.png").write_bytes(PNG_BYTES)
    store = PageImageStore(tmp_path / "pages")
    assert store.resolve("../secret.png") is None
    assert store.resolve(str(tmp_path / "secret.png")) is None
    assert store.resolve(f"{DOC_ID}/9.png") is None


def test_upload_stores_each_page_image_and_serves_it(client):
    pdf = _pdf(2)
    records = client.post("/api/documents/upload", files={"file": ("d.pdf", pdf, "application/pdf")}).json()
    document_id = hashlib.sha256(pdf).hexdigest()
    assert [r["image_path"] for r in records] == [f"{document_id}/1.png", f"{document_id}/2.png"]

    bodies = []
    for record in records:
        response = client.get(f"/api/records/{record['id']}/image")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        bodies.append(response.content)
    assert bodies[0] != bodies[1]
    pixmap = pymupdf.Pixmap(bodies[0])
    assert (pixmap.width, pixmap.height) == (300, 600)  # 72x144 pt rendered at 300 DPI


def test_uploaded_image_file_is_served_unchanged(client):
    record = client.post("/api/documents/upload", files={"file": ("p.png", PNG_BYTES, "image/png")}).json()[0]
    response = client.get(f"/api/records/{record['id']}/image")
    assert response.status_code == 200 and response.content == PNG_BYTES


def test_image_storage_failure_keeps_the_extracted_records(client, monkeypatch, page_image_store):
    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(page_image_store, "save", fail)
    response = client.post("/api/documents/upload", files={"file": ("d.pdf", _pdf(2), "application/pdf")})
    assert response.status_code == 200
    records = response.json()
    assert len(records) == 2 and all(r["image_path"] is None for r in records)
    assert client.get(f"/api/records/{records[0]['id']}/image").status_code == 404


def test_image_endpoint_404s(client, db_session, page_image_store):
    assert client.get("/api/records/missing/image").json() == {"detail": "Record not found"}

    [no_image] = RecordService(db_session).save_records([_record(1)], "b" * 64)
    assert client.get(f"/api/records/{no_image}/image").json() == {"detail": "Page image not available"}

    key = page_image_store.save("c" * 64, 1, PNG_BYTES)
    [deleted] = RecordService(db_session).save_records([_record(1).model_copy(update={"image_path": key})], "c" * 64)
    page_image_store.resolve(key)[0].unlink()
    assert client.get(f"/api/records/{deleted}/image").status_code == 404
