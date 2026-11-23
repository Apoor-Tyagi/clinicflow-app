"""Clinic API: patients and appointments for one clinic at a time.

The clinic comes from the API key, never from the request body, and the database
enforces the boundary again with row-level security."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.auth import current_clinic, hash_key, new_api_key, require_admin
from app.db import clinic_session, connect

router = APIRouter(prefix="/api/v1")

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


# ---- models ----------------------------------------------------------------

class ClinicIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ClinicCreated(BaseModel):
    id: UUID
    name: str
    api_key: str  # shown once, only its hash is stored


class PatientIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)


class PatientOut(BaseModel):
    id: UUID
    full_name: str
    phone: str | None
    email: str | None
    created_at: datetime


class AppointmentIn(BaseModel):
    patient_id: UUID
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def must_have_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("starts_at must include a time zone, for example 2026-12-01T10:00:00+05:30")
        return value


class AppointmentPatch(BaseModel):
    status: Literal["booked", "completed", "no_show", "cancelled"]


class AppointmentOut(BaseModel):
    id: UUID
    patient_id: UUID
    starts_at: datetime
    status: str
    reminder: str | None = None  # pending, scheduled, cancelled, or None when no reminder applies
    created_at: datetime


# ---- admin -----------------------------------------------------------------

@router.post("/admin/clinics", status_code=status.HTTP_201_CREATED,
             response_model=ClinicCreated, dependencies=[Depends(require_admin)])
def create_clinic(body: ClinicIn):
    api_key = new_api_key()
    with connect() as conn:
        row = conn.execute(
            "SELECT create_clinic(%s, %s) AS id", (body.name, hash_key(api_key))
        ).fetchone()
    return {"id": row["id"], "name": body.name, "api_key": api_key}


# ---- patients --------------------------------------------------------------

PATIENT_COLUMNS = "id, full_name, phone, email, created_at"


@router.post("/patients", status_code=status.HTTP_201_CREATED, response_model=PatientOut)
def create_patient(body: PatientIn, clinic_id: UUID = Depends(current_clinic)):
    with clinic_session(clinic_id) as conn:
        return conn.execute(
            f"INSERT INTO patients (clinic_id, full_name, phone, email) "
            f"VALUES (%s, %s, %s, %s) RETURNING {PATIENT_COLUMNS}",
            (clinic_id, body.full_name, body.phone, body.email),
        ).fetchone()


@router.get("/patients", response_model=list[PatientOut])
def list_patients(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                  clinic_id: UUID = Depends(current_clinic)):
    with clinic_session(clinic_id) as conn:
        return conn.execute(
            f"SELECT {PATIENT_COLUMNS} FROM patients ORDER BY created_at, id LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()


@router.get("/patients/{patient_id}", response_model=PatientOut)
def get_patient(patient_id: UUID, clinic_id: UUID = Depends(current_clinic)):
    with clinic_session(clinic_id) as conn:
        row = conn.execute(
            f"SELECT {PATIENT_COLUMNS} FROM patients WHERE id = %s", (patient_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "patient not found")
    return row


# ---- appointments ----------------------------------------------------------

APPOINTMENT_SELECT = (
    "SELECT a.id, a.patient_id, a.starts_at, a.status, a.created_at, r.status AS reminder "
    "FROM appointments a LEFT JOIN reminders r ON r.appointment_id = a.id"
)


@router.post("/appointments", status_code=status.HTTP_201_CREATED, response_model=AppointmentOut)
def book_appointment(body: AppointmentIn, clinic_id: UUID = Depends(current_clinic)):
    if body.starts_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "starts_at must be in the future")
    with clinic_session(clinic_id) as conn:
        # Row-level security hides other clinics' patients, so they look missing.
        patient = conn.execute(
            "SELECT email FROM patients WHERE id = %s", (body.patient_id,)
        ).fetchone()
        if patient is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "patient not found")
        appointment = conn.execute(
            "INSERT INTO appointments (clinic_id, patient_id, starts_at) VALUES (%s, %s, %s) "
            "RETURNING id",
            (clinic_id, body.patient_id, body.starts_at),
        ).fetchone()
        if patient["email"]:
            # Remind 24 hours before, or straight away when the visit is sooner than that.
            conn.execute(
                "INSERT INTO reminders (appointment_id, clinic_id, remind_at) "
                "VALUES (%s, %s, GREATEST(%s::timestamptz - interval '24 hours', now()))",
                (appointment["id"], clinic_id, body.starts_at),
            )
        return conn.execute(
            APPOINTMENT_SELECT + " WHERE a.id = %s", (appointment["id"],)
        ).fetchone()


@router.get("/appointments", response_model=list[AppointmentOut])
def list_appointments(
    starts_after: datetime | None = None,
    starts_before: datetime | None = None,
    status_filter: Literal["booked", "completed", "no_show", "cancelled"] | None = Query(
        None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    clinic_id: UUID = Depends(current_clinic),
):
    where, params = [], []
    if starts_after:
        where.append("a.starts_at >= %s")
        params.append(starts_after)
    if starts_before:
        where.append("a.starts_at < %s")
        params.append(starts_before)
    if status_filter:
        where.append("a.status = %s")
        params.append(status_filter)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with clinic_session(clinic_id) as conn:
        return conn.execute(
            APPOINTMENT_SELECT + clause + " ORDER BY a.starts_at, a.id LIMIT %s OFFSET %s",
            (*params, limit, offset),
        ).fetchall()


@router.get("/appointments/{appointment_id}", response_model=AppointmentOut)
def get_appointment(appointment_id: UUID, clinic_id: UUID = Depends(current_clinic)):
    with clinic_session(clinic_id) as conn:
        row = conn.execute(
            APPOINTMENT_SELECT + " WHERE a.id = %s", (appointment_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "appointment not found")
    return row


@router.patch("/appointments/{appointment_id}", response_model=AppointmentOut)
def update_appointment(appointment_id: UUID, body: AppointmentPatch,
                       clinic_id: UUID = Depends(current_clinic)):
    with clinic_session(clinic_id) as conn:
        updated = conn.execute(
            "UPDATE appointments SET status = %s WHERE id = %s RETURNING id",
            (body.status, appointment_id),
        ).fetchone()
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "appointment not found")
        if body.status != "booked":
            # No reminder for a visit that is cancelled, done or missed.
            conn.execute(
                "UPDATE reminders SET status = 'cancelled' "
                "WHERE appointment_id = %s AND status <> 'cancelled'",
                (appointment_id,),
            )
        return conn.execute(
            APPOINTMENT_SELECT + " WHERE a.id = %s", (appointment_id,)
        ).fetchone()
