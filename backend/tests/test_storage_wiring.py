"""The image endpoint and upload flow on top of private Supabase Storage (simulated)."""

import logging

import httpx
import pytest
from pydantic import SecretStr

from backend.app.api import routes
from backend.app.config import Settings
from backend.app.main import app
from backend.app.services.page_image_store import LocalPageImageStore
from backend.app.services.storage_factory import (
    StorageNotConfiguredError,
    build_page_image_store,
    verify_page_image_store,
)
from backend.app.services.supabase_image_store import PublicBucketError, SupabasePageImageStore
from backend.tests.fakes import PNG_BYTES
from fastapi.testclient import TestClient

from backend.app.services.document_pipeline import DocumentPipeline
from backend.tests.test_page_images import _extractor, _pdf
from backend.tests.test_supabase_image_store import BASE, SERVICE_KEY, FakeStorage


@pytest.fixture
def client():
    app.dependency_overrides[routes.get_document_pipeline] = lambda: DocumentPipeline(_extractor, 3)
    yield TestClient(app)
    app.dependency_overrides.pop(routes.get_document_pipeline, None)


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def supabase_client(client, storage):
    store = SupabasePageImageStore(
        url=BASE,
        service_role_key=SERVICE_KEY,
        bucket="page-images",
        max_retries=1,
        client=httpx.Client(transport=httpx.MockTransport(storage.handler)),
        sleep=lambda _seconds: None,
    )
    app.dependency_overrides[routes.get_page_image_store] = lambda: store
    return client


def _upload(client, pages=2, content=None):
    content = content if content is not None else _pdf(pages)
    response = client.post("/api/documents/upload", files={"file": ("d.pdf", content, "application/pdf")})
    assert response.status_code == 200
    return response.json()


def test_upload_stores_pages_in_bucket_and_image_endpoint_serves_them(supabase_client, storage):
    records = _upload(supabase_client)
    keys = [f"{records[0]['document_id']}/page_{n}.png" for n in (1, 2)]
    assert [r["image_path"] for r in records] == keys
    assert sorted(storage.objects) == keys
    response = supabase_client.get(f"/api/records/{records[1]['id']}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.content == storage.objects[keys[1]][0]
    assert "supabase" not in response.text.lower()


def test_failed_page_upload_leaves_only_that_record_without_image(supabase_client, storage, caplog):
    caplog.set_level(logging.DEBUG)
    # Pages are uploaded in order; both attempts (max_retries=1) for page 1 fail.
    storage.fail_next = [503, 503]
    document = _pdf(2)
    records = _upload(supabase_client, content=document)
    assert records[0]["image_path"] is None
    assert records[1]["image_path"] == f"{records[1]['document_id']}/page_2.png"
    assert supabase_client.get(f"/api/records/{records[0]['id']}/image").status_code == 404
    assert supabase_client.get(f"/api/records/{records[1]['id']}/image").status_code == 200
    assert SERVICE_KEY not in caplog.text

    retried = _upload(supabase_client, content=document)
    assert [r["image_path"] for r in retried] == [f"{records[0]['document_id']}/page_{n}.png" for n in (1, 2)]


def test_unreachable_storage_still_saves_records_without_images(supabase_client, storage):
    storage.fail_next = [httpx.ConnectError("down")] * 4
    records = _upload(supabase_client)
    assert [r["page_number"] for r in records] == [1, 2]
    assert all(r["image_path"] is None for r in records)
    assert storage.objects == {}
    assert supabase_client.get(f"/api/records/{records[0]['id']}/image").status_code == 404


def test_image_endpoint_returns_503_when_storage_is_down_for_a_stored_image(supabase_client, storage):
    records = _upload(supabase_client, pages=1)
    storage.fail_next = [httpx.ConnectError("down")] * 2
    response = supabase_client.get(f"/api/records/{records[0]['id']}/image")
    assert response.status_code == 503
    assert response.json() == {"detail": "Page image storage unavailable"}
    assert BASE not in response.text


def test_missing_bucket_object_returns_404(supabase_client, storage):
    records = _upload(supabase_client, pages=1)
    storage.objects.clear()
    response = supabase_client.get(f"/api/records/{records[0]['id']}/image")
    assert response.status_code == 404
    assert response.json() == {"detail": "Page image not available"}


def test_factory_builds_local_store_by_default(tmp_path):
    store = build_page_image_store(Settings(PAGE_IMAGE_DIR=tmp_path))
    assert isinstance(store, LocalPageImageStore)


def test_factory_builds_supabase_store_from_settings():
    store = build_page_image_store(
        Settings(
            PAGE_IMAGE_STORAGE="supabase",
            SUPABASE_URL=BASE,
            SUPABASE_SERVICE_ROLE_KEY=SecretStr(SERVICE_KEY),
            SUPABASE_STORAGE_BUCKET="medical-pages",
        )
    )
    assert isinstance(store, SupabasePageImageStore)
    assert SERVICE_KEY not in repr(store)


@pytest.mark.parametrize(
    "overrides",
    [{}, {"SUPABASE_URL": BASE}, {"SUPABASE_SERVICE_ROLE_KEY": SecretStr(SERVICE_KEY)}],
)
def test_factory_rejects_incomplete_supabase_settings(overrides):
    with pytest.raises(StorageNotConfiguredError):
        build_page_image_store(Settings(PAGE_IMAGE_STORAGE="supabase", **overrides))


def test_settings_hide_secrets():
    settings = Settings(
        DATABASE_URL=SecretStr("postgresql://user:db-password@host/db"),
        SUPABASE_SERVICE_ROLE_KEY=SecretStr(SERVICE_KEY),
    )
    dumped = repr(settings) + str(settings.model_dump())
    assert SERVICE_KEY not in dumped and "db-password" not in dumped


def test_unconfigured_storage_makes_image_routes_503(client, monkeypatch):
    app.dependency_overrides.pop(routes.get_page_image_store, None)

    def unconfigured():
        raise StorageNotConfiguredError("missing")

    monkeypatch.setattr(routes, "build_configured_page_image_store", unconfigured)
    response = client.get("/api/records/anything/image")
    assert response.status_code == 503
    assert response.json() == {"detail": "Page image storage is not configured"}


def test_startup_check_fails_on_public_bucket_and_tolerates_outage():
    def store_for(storage):
        return SupabasePageImageStore(
            url=BASE,
            service_role_key=SERVICE_KEY,
            bucket="page-images",
            max_retries=0,
            client=httpx.Client(transport=httpx.MockTransport(storage.handler)),
        )

    with pytest.raises(PublicBucketError):
        verify_page_image_store(store_for(FakeStorage(bucket_public=True)))
    down = FakeStorage()
    down.fail_next = [httpx.ConnectError("down")]
    verify_page_image_store(store_for(down))


def test_local_store_load_round_trip(tmp_path):
    store = LocalPageImageStore(tmp_path)
    key = store.save("c" * 64, 4, PNG_BYTES)
    stored = store.load(key)
    assert stored.content == PNG_BYTES and stored.media_type == "image/png"
    assert store.load("c" * 64 + "/page_5.png") is None
