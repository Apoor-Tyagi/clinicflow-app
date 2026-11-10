from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_ping():
    assert client.get("/api/v1/ping").json() == {"message": "pong"}


def test_ready_without_database_is_503():
    assert client.get("/ready").status_code == 503