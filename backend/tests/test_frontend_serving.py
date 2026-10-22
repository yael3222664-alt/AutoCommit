import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.frontend import mount_frontend


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root></div>")
    (root / "assets" / "app.js").write_text("console.log(1)")
    (root / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside")
    return root


def client_for(dist) -> TestClient:
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    mount_frontend(app, dist)
    return TestClient(app)


def test_serves_index_assets_and_root_files(dist):
    client = client_for(dist)
    index = client.get("/")
    assert index.status_code == 200 and "id=root" in index.text
    assert index.headers["cache-control"] == "no-cache"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/favicon.svg").status_code == 200


def test_unknown_paths_fall_back_to_index_for_client_routing(dist):
    assert "id=root" in client_for(dist).get("/records/abc").text


def test_api_routes_win_and_unknown_api_paths_stay_404(dist):
    client = client_for(dist)
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/nope").status_code == 404
    assert client.get("/api").status_code == 404


def test_path_traversal_does_not_escape_dist(dist):
    client = client_for(dist)
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        assert "outside" not in client.get(path).text


def test_missing_build_is_rejected(tmp_path):
    with pytest.raises(RuntimeError):
        mount_frontend(FastAPI(), tmp_path)
