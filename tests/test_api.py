import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app

ADMIN_URL = os.getenv("TENANT_TEST_ADMIN_URL")
APP_URL = os.getenv("TENANT_TEST_APP_URL")
ADMIN_TOKEN = "test-admin-token"

needs_db = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL),
    reason="set TENANT_TEST_ADMIN_URL and TENANT_TEST_APP_URL to run these tests",
)


def test_metrics_label_uses_the_route_template_not_the_id():
    client = TestClient(app)
    some_id = uuid.uuid4()
    client.get(f"/api/v1/patients/{some_id}")  # 401, but the route matched
    metrics = client.get("/metrics").text
    assert "/api/v1/patients/{patient_id}" in metrics
    assert str(some_id) not in metrics


def test_admin_api_is_disabled_without_a_token(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    response = TestClient(app).post("/api/v1/admin/clinics", json={"name": "X"})
    assert response.status_code == 503


def test_clinic_endpoints_require_an_api_key():
    client = TestClient(app)
    assert client.get("/api/v1/patients").status_code == 401


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", APP_URL)
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    return TestClient(app)


@pytest.fixture
def make_clinic(client):
    created = []

    def make(name):
        response = client.post(
            "/api/v1/admin/clinics", json={"name": name}, headers={"X-Admin-Token": ADMIN_TOKEN}
        )
        assert response.status_code == 201
        body = response.json()
        created.append(body["id"])
        return body["id"], {"X-API-Key": body["api_key"]}

    yield make

    if created:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            for table, column in (
                ("reminders", "clinic_id"), ("appointments", "clinic_id"),
                ("patients", "clinic_id"), ("clinic_api_keys", "clinic_id"), ("clinics", "id"),
            ):
                admin.execute(f"DELETE FROM {table} WHERE {column} = ANY(%s::uuid[])", (created,))


def in_days(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def reminder_row(appointment_id):
    with psycopg.connect(ADMIN_URL) as admin:
        return admin.execute(
            "SELECT r.remind_at, a.starts_at, r.status FROM reminders r "
            "JOIN appointments a ON a.id = r.appointment_id WHERE r.appointment_id = %s",
            (appointment_id,),
        ).fetchone()


@needs_db
def test_wrong_admin_token_is_rejected(client):
    response = client.post("/api/v1/admin/clinics", json={"name": "X"},
                           headers={"X-Admin-Token": "wrong"})
    assert response.status_code == 401


@needs_db
def test_invalid_api_key_is_rejected(client):
    assert client.get("/api/v1/patients", headers={"X-API-Key": "cf_nope"}).status_code == 401


@needs_db
def test_clinic_creates_and_reads_its_patients(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    created = client.post("/api/v1/patients", headers=headers,
                          json={"full_name": "Alice", "email": "alice@example.com"})
    assert created.status_code == 201
    patient_id = created.json()["id"]
    assert client.get(f"/api/v1/patients/{patient_id}", headers=headers).json()["full_name"] == "Alice"
    assert [p["id"] for p in client.get("/api/v1/patients", headers=headers).json()] == [patient_id]


@needs_db
def test_bad_patient_input_is_rejected(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    assert client.post("/api/v1/patients", headers=headers, json={"full_name": ""}).status_code == 422
    bad = client.post("/api/v1/patients", headers=headers,
                      json={"full_name": "A", "email": "not-an-email"})
    assert bad.status_code == 422


@needs_db
def test_clinics_cannot_see_each_others_data(client, make_clinic):
    _, headers_a = make_clinic("Clinic A")
    _, headers_b = make_clinic("Clinic B")
    patient = client.post("/api/v1/patients", headers=headers_a,
                          json={"full_name": "Alice", "email": "alice@example.com"}).json()
    appointment = client.post("/api/v1/appointments", headers=headers_a,
                              json={"patient_id": patient["id"], "starts_at": in_days(3)}).json()

    assert client.get("/api/v1/patients", headers=headers_b).json() == []
    assert client.get(f"/api/v1/patients/{patient['id']}", headers=headers_b).status_code == 404
    assert client.get("/api/v1/appointments", headers=headers_b).json() == []
    assert client.get(f"/api/v1/appointments/{appointment['id']}", headers=headers_b).status_code == 404
    # B cannot book for A's patient or change A's appointment
    stolen = client.post("/api/v1/appointments", headers=headers_b,
                         json={"patient_id": patient["id"], "starts_at": in_days(3)})
    assert stolen.status_code == 404
    patch = client.patch(f"/api/v1/appointments/{appointment['id']}", headers=headers_b,
                         json={"status": "cancelled"})
    assert patch.status_code == 404
    assert client.get(f"/api/v1/appointments/{appointment['id']}",
                      headers=headers_a).json()["status"] == "booked"


@needs_db
def test_booking_queues_a_reminder_24_hours_ahead(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    patient = client.post("/api/v1/patients", headers=headers,
                          json={"full_name": "Alice", "email": "alice@example.com"}).json()
    response = client.post("/api/v1/appointments", headers=headers,
                           json={"patient_id": patient["id"], "starts_at": in_days(3)})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "booked" and body["reminder"] == "pending"
    remind_at, starts_at, status = reminder_row(body["id"])
    assert starts_at - remind_at == timedelta(hours=24)
    assert status == "pending"


@needs_db
def test_visit_within_a_day_is_reminded_immediately(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    patient = client.post("/api/v1/patients", headers=headers,
                          json={"full_name": "Alice", "email": "alice@example.com"}).json()
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    body = client.post("/api/v1/appointments", headers=headers,
                       json={"patient_id": patient["id"], "starts_at": soon}).json()
    remind_at, starts_at, _ = reminder_row(body["id"])
    assert remind_at < starts_at - timedelta(hours=1)
    assert remind_at <= datetime.now(timezone.utc)


@needs_db
def test_patient_without_email_gets_no_reminder(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    patient = client.post("/api/v1/patients", headers=headers, json={"full_name": "Bob"}).json()
    body = client.post("/api/v1/appointments", headers=headers,
                       json={"patient_id": patient["id"], "starts_at": in_days(3)}).json()
    assert body["reminder"] is None
    assert reminder_row(body["id"]) is None


@needs_db
def test_bad_appointment_times_are_rejected(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    patient = client.post("/api/v1/patients", headers=headers,
                          json={"full_name": "Alice", "email": "alice@example.com"}).json()
    past = client.post("/api/v1/appointments", headers=headers,
                       json={"patient_id": patient["id"], "starts_at": in_days(-1)})
    naive = client.post("/api/v1/appointments", headers=headers,
                        json={"patient_id": patient["id"], "starts_at": "2030-01-01T10:00:00"})
    assert past.status_code == 422
    assert naive.status_code == 422


@needs_db
def test_cancelling_cancels_the_reminder(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    patient = client.post("/api/v1/patients", headers=headers,
                          json={"full_name": "Alice", "email": "alice@example.com"}).json()
    appointment = client.post("/api/v1/appointments", headers=headers,
                              json={"patient_id": patient["id"], "starts_at": in_days(3)}).json()
    response = client.patch(f"/api/v1/appointments/{appointment['id']}", headers=headers,
                            json={"status": "cancelled"})
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["reminder"] == "cancelled"


@needs_db
def test_appointments_can_be_filtered_by_status(client, make_clinic):
    _, headers = make_clinic("Clinic A")
    patient = client.post("/api/v1/patients", headers=headers, json={"full_name": "Bob"}).json()
    keep = client.post("/api/v1/appointments", headers=headers,
                       json={"patient_id": patient["id"], "starts_at": in_days(2)}).json()
    drop = client.post("/api/v1/appointments", headers=headers,
                       json={"patient_id": patient["id"], "starts_at": in_days(4)}).json()
    client.patch(f"/api/v1/appointments/{drop['id']}", headers=headers, json={"status": "cancelled"})
    booked = client.get("/api/v1/appointments", headers=headers, params={"status": "booked"}).json()
    assert [a["id"] for a in booked] == [keep["id"]]
