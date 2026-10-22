import logging

import httpx
import pytest

from backend.app.services.page_image_store import (
    InvalidPageImageError,
    PageImageStorageError,
)
from backend.app.services.supabase_image_store import PublicBucketError, SupabasePageImageStore
from backend.tests.fakes import JPEG_BYTES, PNG_BYTES

DOC_ID = "a" * 64
BASE = "https://project.supabase.co"
SERVICE_KEY = "sb_secret_TEST-ONLY-NOT-A-REAL-KEY"


class FakeStorage:
    """In-memory Supabase Storage API (the subset the adapter uses)."""

    def __init__(self, bucket_public: bool = False) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.requests: list[httpx.Request] = []
        self.bucket_public = bucket_public
        self.fail_next: list[object] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(failure, json={"message": "unavailable"})
        if request.headers.get("apikey") != SERVICE_KEY:
            return httpx.Response(400, json={"statusCode": "403", "error": "Unauthorized"})
        path = request.url.path.removeprefix("/storage/v1")
        if request.method == "GET" and path == "/bucket/page-images":
            return httpx.Response(200, json={"id": "page-images", "public": self.bucket_public})
        if request.method == "GET" and path.startswith("/bucket/"):
            return httpx.Response(400, json={"statusCode": "404", "error": "Bucket not found"})
        if request.method == "POST" and path.startswith("/object/page-images/"):
            key = path.removeprefix("/object/page-images/")
            if key in self.objects and request.headers.get("x-upsert") != "true":
                return httpx.Response(400, json={"statusCode": "409", "error": "Duplicate"})
            self.objects[key] = (request.content, request.headers["content-type"])
            return httpx.Response(200, json={"Key": f"page-images/{key}"})
        if request.method == "GET" and path.startswith("/object/authenticated/page-images/"):
            key = path.removeprefix("/object/authenticated/page-images/")
            if key not in self.objects:
                body = {"statusCode": "404", "error": "not_found", "message": "Object not found"}
                return httpx.Response(400, json=body)
            content, media_type = self.objects[key]
            return httpx.Response(200, content=content, headers={"content-type": media_type})
        return httpx.Response(404)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


def make_store(storage: FakeStorage, **kwargs) -> SupabasePageImageStore:
    kwargs.setdefault("sleep", lambda _seconds: None)
    return SupabasePageImageStore(
        url=BASE,
        service_role_key=SERVICE_KEY,
        bucket="page-images",
        client=httpx.Client(transport=httpx.MockTransport(storage.handler)),
        **kwargs,
    )


def test_upload_uses_deterministic_key_private_authenticated_endpoint_and_upsert(storage):
    store = make_store(storage)
    assert store.save(DOC_ID, 1, PNG_BYTES) == f"{DOC_ID}/page_1.png"
    assert store.save(DOC_ID, 2, JPEG_BYTES) == f"{DOC_ID}/page_2.jpg"
    request = storage.requests[0]
    assert request.url == f"{BASE}/storage/v1/object/page-images/{DOC_ID}/page_1.png"
    assert request.headers["x-upsert"] == "true" and request.headers["content-type"] == "image/png"
    assert storage.objects[f"{DOC_ID}/page_1.png"] == (PNG_BYTES, "image/png")


def test_retrying_an_upload_rewrites_only_its_own_key(storage):
    store = make_store(storage)
    other = "b" * 64
    store.save(other, 1, JPEG_BYTES)
    store.save(DOC_ID, 1, PNG_BYTES)
    store.save(DOC_ID, 1, PNG_BYTES)
    assert storage.objects == {
        f"{other}/page_1.jpg": (JPEG_BYTES, "image/jpeg"),
        f"{DOC_ID}/page_1.png": (PNG_BYTES, "image/png"),
    }


def test_download_returns_bytes_and_media_type(storage):
    store = make_store(storage)
    key = store.save(DOC_ID, 3, JPEG_BYTES)
    stored = store.load(key)
    assert stored.content == JPEG_BYTES and stored.media_type == "image/jpeg"
    assert storage.requests[-1].url.path == f"/storage/v1/object/authenticated/page-images/{key}"


def test_missing_object_returns_none(storage):
    assert make_store(storage).load(f"{DOC_ID}/page_9.png") is None


def test_plain_404_is_also_missing(storage):
    storage.fail_next = [404]
    assert make_store(storage, max_retries=0).load(f"{DOC_ID}/page_1.png") is None


@pytest.mark.parametrize("key", ["../secret.png", f"{DOC_ID}/page_1.gif", "/etc/passwd", f"{DOC_ID}/../x/page_1.png"])
def test_malformed_keys_never_reach_storage(storage, key):
    assert make_store(storage).load(key) is None
    assert storage.requests == []


def test_invalid_input_is_rejected_before_upload(storage):
    store = make_store(storage)
    with pytest.raises(InvalidPageImageError):
        store.save("not-a-digest", 1, PNG_BYTES)
    with pytest.raises(InvalidPageImageError):
        store.save(DOC_ID, 0, PNG_BYTES)
    with pytest.raises(InvalidPageImageError):
        store.save(DOC_ID, 1, b"%PDF-1.7")
    assert storage.requests == []


def test_downloaded_object_of_wrong_type_is_rejected(storage):
    storage.objects[f"{DOC_ID}/page_1.png"] = (b"<html>not an image</html>", "text/html")
    with pytest.raises(PageImageStorageError):
        make_store(storage).load(f"{DOC_ID}/page_1.png")


def test_unreachable_storage_raises_after_retries_without_leaking_details(storage):
    sleeps = []
    storage.fail_next = [httpx.ConnectError(f"connect to {BASE} failed")] * 4
    store = make_store(storage, max_retries=3, retry_base_delay_seconds=0.5, sleep=sleeps.append)
    with pytest.raises(PageImageStorageError) as excinfo:
        store.save(DOC_ID, 1, PNG_BYTES)
    assert len(storage.requests) == 4 and sleeps == [0.5, 1.0, 2.0]
    assert "unreachable" in str(excinfo.value) and SERVICE_KEY not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_unreachable_storage_on_download_raises(storage):
    storage.fail_next = [httpx.ReadTimeout("timed out")] * 2
    with pytest.raises(PageImageStorageError):
        make_store(storage, max_retries=1).load(f"{DOC_ID}/page_1.png")


def test_transient_errors_are_retried_then_succeed(storage):
    storage.fail_next = [503, httpx.ConnectError("reset"), 429]
    assert make_store(storage, max_retries=3).save(DOC_ID, 1, PNG_BYTES) == f"{DOC_ID}/page_1.png"
    assert f"{DOC_ID}/page_1.png" in storage.objects


@pytest.mark.parametrize("status", [400, 401, 403, 413])
def test_rejected_upload_is_not_retried_and_raises(storage, status):
    storage.fail_next = [status]
    with pytest.raises(PageImageStorageError, match=str(status)):
        make_store(storage).save(DOC_ID, 1, PNG_BYTES)
    assert len(storage.requests) == 1 and storage.objects == {}


def test_check_accepts_private_bucket(storage):
    make_store(storage).check()


def test_check_rejects_public_bucket():
    with pytest.raises(PublicBucketError):
        make_store(FakeStorage(bucket_public=True)).check()


def test_check_reports_missing_bucket(storage):
    store = SupabasePageImageStore(
        url=BASE,
        service_role_key=SERVICE_KEY,
        bucket="other-bucket",
        client=httpx.Client(transport=httpx.MockTransport(storage.handler)),
    )
    with pytest.raises(PageImageStorageError, match="does not exist"):
        store.check()


def test_wrong_key_is_reported_without_echoing_it(storage):
    store = SupabasePageImageStore(
        url=BASE,
        service_role_key="sb_secret_WRONG",
        bucket="page-images",
        client=httpx.Client(transport=httpx.MockTransport(storage.handler)),
    )
    with pytest.raises(PageImageStorageError) as excinfo:
        store.save(DOC_ID, 1, PNG_BYTES)
    assert "WRONG" not in str(excinfo.value)


def test_legacy_jwt_service_key_is_also_sent_as_bearer_token(storage):
    legacy_key = "eyJhbGciOiJIUzI1NiJ9.e30.TEST-ONLY"
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = SupabasePageImageStore(url=BASE, service_role_key=legacy_key, bucket="page-images", client=client)
    store.save(DOC_ID, 1, PNG_BYTES)
    assert calls[0].headers["authorization"] == f"Bearer {legacy_key}"


def test_key_never_appears_in_repr_or_logs(storage, caplog):
    caplog.set_level(logging.DEBUG)
    store = make_store(storage)
    storage.fail_next = [500, 500, 500, 500]
    with pytest.raises(PageImageStorageError):
        store.save(DOC_ID, 1, PNG_BYTES)
    assert SERVICE_KEY not in repr(store)
    assert SERVICE_KEY not in caplog.text


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"url": "http://project.supabase.co"}, "https"),
        ({"service_role_key": ""}, "empty"),
        ({"bucket": "../public"}, "bucket"),
    ],
)
def test_invalid_configuration_is_rejected(kwargs, message):
    settings = {"url": BASE, "service_role_key": SERVICE_KEY, "bucket": "page-images", **kwargs}
    with pytest.raises(ValueError, match=message):
        SupabasePageImageStore(**settings)
