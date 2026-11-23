import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from dispatcher.dispatcher import Dispatcher, at_expression, fire_time, schedule_name

ADMIN_URL = os.getenv("TENANT_TEST_ADMIN_URL")
DISPATCHER_URL = os.getenv("TENANT_TEST_DISPATCHER_URL")

needs_db = pytest.mark.skipif(
    not (ADMIN_URL and DISPATCHER_URL),
    reason="set TENANT_TEST_ADMIN_URL and TENANT_TEST_DISPATCHER_URL to run these tests",
)

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)


class AwsError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeScheduler:
    def __init__(self, create_error=None, delete_error=None):
        self.created, self.deleted = [], []
        self.create_error, self.delete_error = create_error, delete_error

    def create_schedule(self, **kwargs):
        if self.create_error:
            raise self.create_error
        self.created.append(kwargs)

    def delete_schedule(self, **kwargs):
        self.deleted.append(kwargs)
        if self.delete_error:
            raise self.delete_error


def test_schedule_fires_at_the_reminder_time_when_it_is_in_the_future():
    remind_at = NOW + timedelta(hours=5)
    assert fire_time(remind_at, NOW) == remind_at


def test_a_reminder_that_is_already_due_fires_a_minute_from_now():
    assert fire_time(NOW - timedelta(hours=3), NOW) == NOW + timedelta(seconds=60)


def test_expression_is_a_utc_one_time_schedule():
    assert at_expression(datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)) == "at(2030-01-02T03:04:05)"


@pytest.fixture
def world():
    """A clinic, a patient and a way to add appointments with reminders."""
    clinic_id, patient_id = uuid.uuid4(), uuid.uuid4()
    admin = psycopg.connect(ADMIN_URL, autocommit=True)
    admin.execute("INSERT INTO clinics (id, name) VALUES (%s, 'Dispatch Test')", (clinic_id,))
    admin.execute(
        "INSERT INTO patients (id, clinic_id, full_name, email) VALUES (%s, %s, 'Test', 'test@example.com')",
        (patient_id, clinic_id),
    )

    def add(remind_in_hours=24, starts_in_hours=48, status="pending"):
        appointment_id = uuid.uuid4()
        starts = datetime.now(timezone.utc) + timedelta(hours=starts_in_hours)
        admin.execute(
            "INSERT INTO appointments (id, clinic_id, patient_id, starts_at) VALUES (%s, %s, %s, %s)",
            (appointment_id, clinic_id, patient_id, starts),
        )
        admin.execute(
            "INSERT INTO reminders (appointment_id, clinic_id, remind_at, status) VALUES (%s, %s, %s, %s)",
            (appointment_id, clinic_id, datetime.now(timezone.utc) + timedelta(hours=remind_in_hours), status),
        )
        return appointment_id, starts

    def row(appointment_id):
        return admin.execute(
            "SELECT status, scheduled_at, released_at FROM reminders WHERE appointment_id = %s",
            (appointment_id,),
        ).fetchone()

    yield add, row, admin

    for table, column in (("reminders", "clinic_id"), ("appointments", "clinic_id"),
                          ("patients", "clinic_id"), ("clinics", "id")):
        admin.execute(f"DELETE FROM {table} WHERE {column} = %s", (clinic_id,))
    admin.close()


def make(scheduler):
    return Dispatcher(
        connect=lambda: psycopg.connect(DISPATCHER_URL),
        scheduler=scheduler,
        group="reminders",
        queue_arn="arn:aws:sqs:us-east-1:123456789012:queue",
        role_arn="arn:aws:iam::123456789012:role/scheduler",
    )


@needs_db
def test_pending_reminder_becomes_a_schedule_with_the_worker_message(world):
    add, row, _ = world
    appointment_id, starts = add()
    scheduler = FakeScheduler()

    make(scheduler).schedule_pending()

    assert len(scheduler.created) == 1
    call = scheduler.created[0]
    assert call["Name"] == schedule_name(appointment_id)
    assert call["GroupName"] == "reminders"
    assert call["ActionAfterCompletion"] == "DELETE"
    message = json.loads(call["Target"]["Input"])
    assert message["appointment_id"] == str(appointment_id)
    assert message["recipient"] == "test@example.com"
    assert message["starts_at"] == starts.isoformat()
    assert set(message) == {"clinic_id", "appointment_id", "recipient", "starts_at"}  # what the worker needs
    status, scheduled_at, _ = row(appointment_id)
    assert status == "scheduled" and scheduled_at is not None


@needs_db
def test_scheduling_twice_does_nothing_the_second_time(world):
    add, _, _ = world
    add()
    scheduler = FakeScheduler()
    dispatcher = make(scheduler)
    dispatcher.schedule_pending()
    dispatcher.schedule_pending()
    assert len(scheduler.created) == 1


@needs_db
def test_an_already_existing_schedule_still_marks_the_row_scheduled(world):
    add, row, _ = world
    appointment_id, _ = add()
    make(FakeScheduler(create_error=AwsError("ConflictException"))).schedule_pending()
    assert row(appointment_id)[0] == "scheduled"


@needs_db
def test_a_failed_call_leaves_the_reminder_pending_for_the_next_try(world):
    add, row, _ = world
    appointment_id, _ = add()
    make(FakeScheduler(create_error=AwsError("ThrottlingException"))).schedule_pending()
    assert row(appointment_id)[0] == "pending"


@needs_db
def test_a_reminder_that_is_already_due_is_scheduled_in_the_future(world):
    add, _, _ = world
    add(remind_in_hours=-5, starts_in_hours=2)
    scheduler = FakeScheduler()
    before = datetime.now(timezone.utc)
    make(scheduler).schedule_pending()
    expression = scheduler.created[0]["ScheduleExpression"]
    fires = datetime.strptime(expression, "at(%Y-%m-%dT%H:%M:%S)").replace(tzinfo=timezone.utc)
    assert fires > before


@needs_db
def test_cancelling_after_scheduling_deletes_the_schedule_once(world):
    add, row, admin = world
    appointment_id, _ = add()
    scheduler = FakeScheduler()
    dispatcher = make(scheduler)
    dispatcher.schedule_pending()
    admin.execute("UPDATE reminders SET status = 'cancelled' WHERE appointment_id = %s", (appointment_id,))

    assert dispatcher.release_cancelled() == 1
    assert dispatcher.release_cancelled() == 0
    assert scheduler.deleted == [{"Name": schedule_name(appointment_id), "GroupName": "reminders"}]
    assert row(appointment_id)[2] is not None


@needs_db
def test_a_schedule_that_is_already_gone_counts_as_released(world):
    add, row, admin = world
    appointment_id, _ = add()
    dispatcher = make(FakeScheduler(delete_error=AwsError("ResourceNotFoundException")))
    dispatcher.schedule_pending()
    admin.execute("UPDATE reminders SET status = 'cancelled' WHERE appointment_id = %s", (appointment_id,))
    dispatcher.release_cancelled()
    assert row(appointment_id)[2] is not None


@needs_db
def test_cancelled_before_scheduling_never_touches_aws(world):
    add, _, _ = world
    add(status="cancelled")
    scheduler = FakeScheduler()
    dispatcher = make(scheduler)
    assert dispatcher.run_once() == (0, 0)
    assert scheduler.created == [] and scheduler.deleted == []
