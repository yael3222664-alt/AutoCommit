from fastapi.testclient import TestClient

from backend.app.api.auth import SESSION_COOKIE, LoginRateLimiter, SessionSigner
from backend.app.config import Settings
from backend.app import main
from backend.app.main import create_app

PASSWORD = "correct horse battery"
SECRET = "s" * 40


def auth_settings(**overrides) -> Settings:
    values = dict(_env_file=None, AUTH_ENABLED=True, APP_ACCESS_PASSWORD=PASSWORD, SESSION_SECRET=SECRET,
                  LOGIN_MAX_FAILURES=3)
    values.update(overrides)
    return Settings(**values)


def client_for(settings: Settings) -> TestClient:
    app = create_app(settings)
    # Share the per-test database and image-store overrides installed on the default app.
    app.dependency_overrides = main.app.dependency_overrides
    return TestClient(app)


def auth_client(**overrides) -> TestClient:
    return client_for(auth_settings(**overrides))


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_session_signer_accepts_its_own_token_until_expiry():
    clock = Clock()
    signer = SessionSigner(b"k" * 32, max_age_seconds=60, clock=clock)
    token = signer.issue()
    assert signer.verify(token)
    clock.now += 61
    assert not signer.verify(token)


def test_session_signer_rejects_tampered_and_foreign_tokens():
    signer = SessionSigner(b"k" * 32, max_age_seconds=60)
    token = signer.issue()
    expires, nonce, signature = token.split(".")
    assert not signer.verify(f"{int(expires) + 999}.{nonce}.{signature}")
    assert not signer.verify(SessionSigner(b"x" * 32, 60).issue())
    for bad in (None, "", "garbage", "a.b", "ש.ש.ש"):
        assert not signer.verify(bad)


def test_rate_limiter_blocks_after_max_failures_and_recovers():
    clock = Clock()
    limiter = LoginRateLimiter(max_failures=2, window_seconds=10, clock=clock)
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 0
    limiter.record_failure("a")
    assert limiter.retry_after("a") > 0
    assert limiter.retry_after("b") == 0
    clock.now += 11
    assert limiter.retry_after("a") == 0


def test_api_requires_login_when_enabled():
    client = auth_client()
    assert client.get("/api/records").status_code == 401
    assert client.get("/api/records/x/image").status_code == 401
    assert client.post("/api/documents/upload").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/session").json() == {"auth_enabled": True, "authenticated": False}


def test_login_sets_hardened_cookie_and_grants_access():
    client = auth_client()
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 204
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert PASSWORD not in cookie
    assert client.get("/api/auth/session").json()["authenticated"] is True
    assert client.get("/api/records").status_code == 200


def test_cookie_is_not_secure_only_outside_production():
    assert "Secure" not in auth_client().post("/api/auth/login", json={"password": PASSWORD}).headers["set-cookie"]


def test_wrong_password_is_rejected_then_rate_limited():
    client = auth_client()
    for _ in range(3):
        response = client.post("/api/auth/login", json={"password": "wrong password!"})
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
    blocked = client.post("/api/auth/login", json={"password": PASSWORD})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_logout_clears_the_session():
    client = auth_client()
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/session").json()["authenticated"] is False
    assert client.get("/api/records").status_code == 401


def test_forged_cookie_is_rejected():
    client = auth_client()
    client.cookies.set(SESSION_COOKIE, "9999999999.nonce.forged")
    assert client.get("/api/records").status_code == 401


def test_auth_disabled_leaves_api_open_and_login_unavailable():
    client = client_for(Settings(_env_file=None))
    assert client.get("/api/auth/session").json() == {"auth_enabled": False, "authenticated": True}
    assert client.get("/api/records").status_code == 200
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 404
    proxied = client.post("/api/auth/logout", headers={"Origin": "http://127.0.0.1:5173"})
    assert proxied.status_code == 204


def test_cross_origin_writes_are_rejected():
    client = auth_client()
    response = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    same_origin = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"Origin": "http://testserver"})
    assert same_origin.status_code == 204
    allowed_dev_origin = client.post("/api/auth/logout", headers={"Origin": "http://localhost:5173"})
    assert allowed_dev_origin.status_code == 204


def test_security_headers_present_and_csp_only_in_production(tmp_path):
    dev = auth_client().get("/api/health")
    assert dev.headers["x-content-type-options"] == "nosniff"
    assert dev.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in dev.headers
    (tmp_path / "index.html").write_text("<!doctype html>")
    prod = client_for(auth_settings(
        ENVIRONMENT="production", DEBUG=False, DATABASE_URL="postgresql://u:p@db.example:6543/postgres",
        PAGE_IMAGE_STORAGE="supabase", SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="k",
        GEMINI_API_KEY="g", FRONTEND_DIST_DIR=tmp_path,
    ))
    response = prod.get("/api/health")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "strict-transport-security" in response.headers
    assert "Secure" in prod.post("/api/auth/login", json={"password": PASSWORD}).headers["set-cookie"]
    for docs_path in ("/docs", "/redoc", "/openapi.json"):
        assert "swagger" not in prod.get(docs_path).text.lower()
        assert prod.get(docs_path).headers["content-type"].startswith("text/html")
