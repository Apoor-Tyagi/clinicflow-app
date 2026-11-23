"""Reminder dispatcher: turns queued reminders into EventBridge schedules.

The API only writes a row to the reminders table, because it runs in a pod with no
AWS credentials. This service runs on the node, where the instance role can create
schedules. For each pending reminder it creates a one-time schedule that sends the
reminder message to the queue at the right moment, then marks the row scheduled.
When an appointment is cancelled after its schedule exists, it deletes the schedule.

Every step can be repeated safely: the schedule name comes from the appointment id,
so a second attempt finds the schedule already there and carries on.
Recipient addresses are never logged.
"""
import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("reminder-dispatcher")

BATCH = 25
# EventBridge rejects a one-time schedule in the past, so leave a small margin.
MIN_LEAD = timedelta(seconds=60)

PENDING_SQL = """
    SELECT r.appointment_id, r.clinic_id, a.starts_at, r.remind_at, p.email
    FROM reminders r
    JOIN appointments a ON a.id = r.appointment_id AND a.clinic_id = r.clinic_id
    JOIN patients p ON p.id = a.patient_id AND p.clinic_id = a.clinic_id
    WHERE r.status = 'pending'
    ORDER BY r.remind_at
    LIMIT %s
"""

CANCELLED_SQL = """
    SELECT appointment_id FROM reminders
    WHERE status = 'cancelled' AND scheduled_at IS NOT NULL AND released_at IS NULL
    LIMIT %s
"""


def schedule_name(appointment_id):
    return f"reminder-{appointment_id}"


def fire_time(remind_at, now):
    """When the reminder should go out: on time, or shortly after now if already due."""
    return max(remind_at, now + MIN_LEAD).astimezone(timezone.utc)


def at_expression(moment):
    return "at(" + moment.strftime("%Y-%m-%dT%H:%M:%S") + ")"


def error_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")


class Dispatcher:
    def __init__(self, connect, scheduler, group, queue_arn, role_arn, clock=None):
        self.connect = connect
        self.scheduler = scheduler
        self.group = group
        self.queue_arn = queue_arn
        self.role_arn = role_arn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self):
        scheduled = self.schedule_pending()
        released = self.release_cancelled()
        return scheduled, released

    def schedule_pending(self):
        with self.connect() as conn:
            rows = conn.execute(PENDING_SQL, (BATCH,)).fetchall()
        done = 0
        for appointment_id, clinic_id, starts_at, remind_at, email in rows:
            message = {
                "clinic_id": str(clinic_id),
                "appointment_id": str(appointment_id),
                "recipient": email,
                "starts_at": starts_at.isoformat(),
            }
            try:
                self.scheduler.create_schedule(
                    Name=schedule_name(appointment_id),
                    GroupName=self.group,
                    ScheduleExpression=at_expression(fire_time(remind_at, self.clock())),
                    FlexibleTimeWindow={"Mode": "OFF"},
                    Target={
                        "Arn": self.queue_arn,
                        "RoleArn": self.role_arn,
                        "Input": json.dumps(message),
                    },
                    ActionAfterCompletion="DELETE",
                )
            except Exception as exc:
                if error_code(exc) != "ConflictException":
                    log.error("could not schedule appointment %s: %s", appointment_id, type(exc).__name__)
                    continue
            with self.connect() as conn:
                # A cancel that arrived meanwhile keeps its status, so it is cleaned up next.
                conn.execute(
                    "UPDATE reminders SET scheduled_at = now(), "
                    "status = CASE WHEN status = 'pending' THEN 'scheduled' ELSE status END "
                    "WHERE appointment_id = %s",
                    (appointment_id,),
                )
            log.info("scheduled reminder for appointment %s", appointment_id)
            done += 1
        return done

    def release_cancelled(self):
        with self.connect() as conn:
            rows = conn.execute(CANCELLED_SQL, (BATCH,)).fetchall()
        done = 0
        for (appointment_id,) in rows:
            try:
                self.scheduler.delete_schedule(Name=schedule_name(appointment_id), GroupName=self.group)
            except Exception as exc:
                # Gone already means it fired or was removed, which is the outcome we want.
                if error_code(exc) != "ResourceNotFoundException":
                    log.error("could not delete schedule for %s: %s", appointment_id, type(exc).__name__)
                    continue
            with self.connect() as conn:
                conn.execute(
                    "UPDATE reminders SET released_at = now() WHERE appointment_id = %s",
                    (appointment_id,),
                )
            log.info("released schedule for appointment %s", appointment_id)
            done += 1
        return done


def main():
    import boto3  # imported here so the tests do not need boto3
    import psycopg

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    region = os.environ["AWS_REGION"]
    url = os.environ["DATABASE_URL"]
    dispatcher = Dispatcher(
        connect=lambda: psycopg.connect(url, connect_timeout=5),
        scheduler=boto3.client("scheduler", region_name=region),
        group=os.environ["SCHEDULE_GROUP"],
        queue_arn=os.environ["QUEUE_ARN"],
        role_arn=os.environ["SCHEDULER_ROLE_ARN"],
    )
    interval = int(os.getenv("POLL_SECONDS", "15"))

    state = {"stop": False}

    def request_stop(signum, frame):
        state["stop"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log.info("dispatcher started")
    while not state["stop"]:
        try:
            dispatcher.run_once()
        except Exception as exc:
            log.error("poll failed: %s", type(exc).__name__)
        for _ in range(interval):
            if state["stop"]:
                break
            time.sleep(1)
    log.info("dispatcher stopped")


if __name__ == "__main__":
    main()
