from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_ping():
    assert client.get("/api/v1/ping").json() == {"message": "pong"}


def test_ready_without_database_is_503():
    assert client.get("/ready").status_code == 503

def test_ping_fails_when_fault_rate_is_one(monkeypatch):
    import app.main as m

    monkeypatch.setattr(m, "FAULT_RATE", 1.0)
    assert client.get("/api/v1/ping").status_code == 500


def test_ping_ok_when_fault_rate_is_zero(monkeypatch):
    import app.main as m

    monkeypatch.setattr(m, "FAULT_RATE", 0.0)
    assert client.get("/api/v1/ping").json() == {"message": "pong"}
