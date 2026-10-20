from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.main import app


def test_health_returns_ok():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": settings.APP_NAME}
