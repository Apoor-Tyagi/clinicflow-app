import os
import uuid

import psycopg
import pytest

ADMIN_URL = os.getenv("TENANT_TEST_ADMIN_URL")
APP_URL = os.getenv("TENANT_TEST_APP_URL")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and APP_URL),
    reason="set TENANT_TEST_ADMIN_URL and TENANT_TEST_APP_URL to run these tests",
)


def as_clinic(clinic_id):
    """Open a connection as the app role, acting for one clinic (or none)."""
    conn = psycopg.connect(APP_URL)
    if clinic_id is not None:
        conn.execute("SELECT set_config('app.clinic_id', %s, true)", (str(clinic_id),))
    return conn


@pytest.fixture
def tenants():
    ids = {
        "clinic_a": uuid.uuid4(),
        "clinic_b": uuid.uuid4(),
        "patient_a": uuid.uuid4(),
        "patient_b": uuid.uuid4(),
    }
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(
            "INSERT INTO clinics (id, name) VALUES (%s, 'Clinic A'), (%s, 'Clinic B')",
            (ids["clinic_a"], ids["clinic_b"]),
        )
        admin.execute(
            "INSERT INTO patients (id, clinic_id, full_name) "
            "VALUES (%s, %s, 'Alice A'), (%s, %s, 'Bob B')",
            (ids["patient_a"], ids["clinic_a"], ids["patient_b"], ids["clinic_b"]),
        )
        yield ids
        both = (ids["clinic_a"], ids["clinic_b"])
        admin.execute("DELETE FROM appointments WHERE clinic_id IN (%s, %s)", both)
        admin.execute("DELETE FROM patients WHERE clinic_id IN (%s, %s)", both)
        admin.execute("DELETE FROM clinics WHERE id IN (%s, %s)", both)


def test_clinic_sees_only_its_own_patients(tenants):
    with as_clinic(tenants["clinic_a"]) as conn:
        names = [r[0] for r in conn.execute("SELECT full_name FROM patients").fetchall()]
    assert names == ["Alice A"]


def test_no_clinic_selected_sees_nothing(tenants):
    with as_clinic(None) as conn:
        assert conn.execute("SELECT count(*) FROM patients").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM clinics").fetchone()[0] == 0


def test_cannot_insert_a_row_for_another_clinic(tenants):
    with as_clinic(tenants["clinic_a"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO patients (clinic_id, full_name) VALUES (%s, 'Mallory')",
                (tenants["clinic_b"],),
            )


def test_cannot_update_another_clinics_patient(tenants):
    with as_clinic(tenants["clinic_a"]) as conn:
        cur = conn.execute(
            "UPDATE patients SET full_name = 'hacked' WHERE id = %s",
            (tenants["patient_b"],),
        )
        assert cur.rowcount == 0
    with psycopg.connect(ADMIN_URL) as admin:
        name = admin.execute(
            "SELECT full_name FROM patients WHERE id = %s", (tenants["patient_b"],)
        ).fetchone()[0]
    assert name == "Bob B"


def test_cannot_book_a_patient_of_another_clinic(tenants):
    with as_clinic(tenants["clinic_a"]) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO appointments (clinic_id, patient_id, starts_at) "
                "VALUES (%s, %s, now())",
                (tenants["clinic_a"], tenants["patient_b"]),
            )


def test_app_role_cannot_bypass_row_level_security():
    with psycopg.connect(APP_URL) as conn:
        superuser, bypass = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
    assert (superuser, bypass) == (False, False)
