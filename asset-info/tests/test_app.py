from fastapi.testclient import TestClient

from app.main import app


def test_application_starts() -> None:
    with TestClient(app) as client:
        assert client.app is app


def test_liveness() -> None:
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "asset-information-api"}


def test_readiness() -> None:
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
