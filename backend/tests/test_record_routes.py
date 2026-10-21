from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from backend.app.api import routes
from backend.app.config import get_settings
from backend.app.db.models import RecordRow
from backend.app.main import app
from backend.app.services.document_pipeline import DocumentPipeline, build_failed_record
from backend.app.services.record_service import RecordService
from backend.tests.fakes import PNG_BYTES
from backend.tests.test_documents_api import RecordingExtractor, _pdf
from backend.tests.test_record_service import BASE_TIME, VALID_ID, complete_record, incomplete_record


@pytest.fixture
def client():
    yield TestClient(app)
    app.dependency_overrides.pop(routes.get_document_pipeline, None)


@pytest.fixture
def seed(db_sessionmaker):
    def make(records, document_id="doc-a"):
        with db_sessionmaker() as session:
            return RecordService(session).save_records(records, document_id)

    return make


def use_extractor(extractor):
    app.dependency_overrides[routes.get_document_pipeline] = lambda: DocumentPipeline(extractor, 3)


def upload(client, content, filename="doc.pdf", content_type="application/pdf"):
    return client.post("/api/documents/upload", files={"file": (filename, content, content_type)})


def test_tests_never_use_the_development_database():
    assert get_settings().DATABASE_URL == "sqlite://"


# --- upload persistence --------------------------------------------------------


def test_upload_persists_successful_and_failed_pages(client):
    use_extractor(RecordingExtractor(fail_pages={2}))
    response = upload(client, _pdf(3))

    assert response.status_code == 200
    body = response.json()
    assert [record["page_number"] for record in body] == [1, 2, 3]
    assert [record["status"] for record in body] == ["pending_review", "extraction_failed", "pending_review"]
    assert len({record["document_id"] for record in body}) == 1
    assert all(record["id"] and record["created_at"] and record["updated_at"] for record in body)

    listed = client.get("/api/records").json()
    assert listed["total"] == 3
    assert {record["id"] for record in listed["items"]} == {record["id"] for record in body}


def test_reuploading_same_file_does_not_duplicate_records(client):
    use_extractor(RecordingExtractor())
    content = _pdf(2)
    first = upload(client, content).json()
    second = upload(client, content).json()

    assert [r["id"] for r in first] == [r["id"] for r in second]
    assert client.get("/api/records").json()["total"] == 2


def test_different_files_create_separate_documents(client):
    use_extractor(RecordingExtractor())
    pdf_records = upload(client, _pdf(1)).json()
    image_records = upload(client, PNG_BYTES, "scan.png", "image/png").json()

    assert pdf_records[0]["document_id"] != image_records[0]["document_id"]
    assert client.get("/api/records").json()["total"] == 2


def test_upload_returns_503_when_persistence_fails(client, monkeypatch):
    def fail(self, records, document_id):
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    monkeypatch.setattr(RecordService, "save_records", fail)
    use_extractor(RecordingExtractor())
    assert upload(client, _pdf(1)).status_code == 503


# --- GET /records --------------------------------------------------------------


def test_get_record_returns_record_with_leading_zeros(client, seed):
    [record_id] = seed([complete_record()])
    response = client.get(f"/api/records/{record_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == record_id
    assert body["document_id"] == "doc-a"
    assert body["id_number"]["value"] == "000000018"
    assert body["central_numbers"][0]["value"] == "000123"
    assert body["commitment_numbers"][0]["value"] == "0070"


def test_get_missing_record_returns_404(client):
    assert client.get("/api/records/does-not-exist").status_code == 404


def test_list_filters_and_paginates(client, seed, db_sessionmaker):
    ids = seed([complete_record(1), incomplete_record(2), build_failed_record(3, "provider_error"), complete_record(4)])
    with db_sessionmaker() as session:
        for day, record_id in enumerate(ids):
            session.execute(
                update(RecordRow).where(RecordRow.id == record_id).values(created_at=BASE_TIME + timedelta(days=day))
            )
        session.commit()

    page = client.get("/api/records", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 4
    assert (page["limit"], page["offset"]) == (2, 1)
    assert [r["id"] for r in page["items"]] == [ids[2], ids[1]]

    filtered = client.get(
        "/api/records",
        params={
            "status": "pending_review",
            "sender": "סורוקה",
            "requires_manual_review": "false",
            "created_from": "2026-01-01T00:00:00Z",
            "created_to": "2026-01-03T00:00:00Z",
        },
    ).json()
    assert [r["id"] for r in filtered["items"]] == [ids[0]]
    assert filtered["total"] == 1

    beyond = client.get("/api/records", params={"offset": 10}).json()
    assert beyond["items"] == [] and beyond["total"] == 4


@pytest.mark.parametrize(
    "params",
    [{"status": "unknown"}, {"limit": 0}, {"limit": 201}, {"offset": -1}, {"created_from": "yesterday"}],
)
def test_list_rejects_invalid_query_parameters(client, params):
    assert client.get("/api/records", params=params).status_code == 422


# --- PATCH /records/{id} -------------------------------------------------------


def test_patch_updates_only_supplied_fields(client, seed):
    [record_id] = seed([complete_record()])
    response = client.patch(f"/api/records/{record_id}", json={"last_name": None, "id_number": "0000018"})

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"]["value"] == "Dana"
    assert body["last_name"]["value"] is None
    assert body["id_number"]["value"] == VALID_ID
    assert body["review_reasons"] == ["last_name_missing"]


def test_patch_approves_complete_record(client, seed):
    [record_id] = seed([complete_record()])
    response = client.patch(f"/api/records/{record_id}", json={"status": "approved"})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert client.get(f"/api/records/{record_id}").json()["status"] == "approved"


def test_patch_missing_record_returns_404(client):
    assert client.patch("/api/records/missing", json={"first_name": "x"}).status_code == 404


def test_patch_illegal_transition_returns_400(client, seed):
    [record_id] = seed([build_failed_record(1, "provider_timeout")])
    response = client.patch(f"/api/records/{record_id}", json={"status": "approved"})

    assert response.status_code == 400
    assert client.get(f"/api/records/{record_id}").json()["status"] == "extraction_failed"


def test_patch_approval_with_missing_fields_returns_400(client, seed):
    [record_id] = seed([incomplete_record()])
    assert client.patch(f"/api/records/{record_id}", json={"status": "approved"}).status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"document_id": "other"},
        {"page_number": 2},
        {"id": "new-id"},
        {"created_at": "2026-01-01"},
        {"requires_manual_review": False},
        {"review_reasons": []},
        {"status": "extraction_failed"},
        {"status": None},
        {"id_number": 18},
        {"central_numbers": [{"value": "  "}]},
        {"received_date": "31/02/2026"},
    ],
)
def test_patch_invalid_payload_returns_422(client, seed, payload):
    [record_id] = seed([complete_record()])
    before = client.get(f"/api/records/{record_id}").json()

    assert client.patch(f"/api/records/{record_id}", json=payload).status_code == 422
    assert client.get(f"/api/records/{record_id}").json() == before
