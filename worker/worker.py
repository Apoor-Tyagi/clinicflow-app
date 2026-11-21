"""Reminder worker: consumes reminder messages from SQS, sends email through SES
and records the outcome in the audit table.

Delivery is at-least-once. A message is deleted only after the email was sent and
the audit record was written. Anything else leaves it on the queue, and after the
queue's maxReceiveCount it moves to the dead-letter queue.
Recipient addresses and message bodies are never logged or stored.
"""
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

log = logging.getLogger("reminder-worker")

REQUIRED = ("clinic_id", "appointment_id", "recipient", "starts_at")


class BadMessage(Exception):
    pass


def parse(body):
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        raise BadMessage("body is not valid JSON")
    if not isinstance(data, dict):
        raise BadMessage("body is not an object")
    for key in REQUIRED:
        if not isinstance(data.get(key), str) or not data[key]:
            raise BadMessage(f"missing or invalid field: {key}")
    if "@" not in data["recipient"]:
        raise BadMessage("recipient is not an email address")
    return data


def error_code(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")


class Worker:
    def __init__(self, sqs, ses, ddb, queue_url, sender, table):
        self.sqs = sqs
        self.ses = ses
        self.ddb = ddb
        self.queue_url = queue_url
        self.sender = sender
        self.table = table

    def handle(self, message):
        """Return True when the message is fully handled and may be deleted."""
        try:
            data = parse(message["Body"])
        except BadMessage as exc:
            log.error("bad message %s: %s", message.get("MessageId"), exc)
            return False

        try:
            self.ses.send_email(
                FromEmailAddress=self.sender,
                Destination={"ToAddresses": [data["recipient"]]},
                Content={
                    "Simple": {
                        "Subject": {"Data": "Appointment reminder"},
                        "Body": {
                            "Text": {
                                "Data": f"This is a reminder of your appointment on {data['starts_at']}."
                            }
                        },
                    }
                },
            )
        except Exception as exc:
            log.error("send failed for appointment %s: %s", data["appointment_id"], type(exc).__name__)
            return False

        try:
            self.ddb.put_item(
                TableName=self.table,
                Item={
                    "clinic_id": {"S": data["clinic_id"]},
                    "event_time": {"S": f"reminder#{data['appointment_id']}#{data['starts_at']}"},
                    "action": {"S": "reminder_sent"},
                    "sent_at": {"S": datetime.now(timezone.utc).isoformat()},
                },
                ConditionExpression="attribute_not_exists(clinic_id)",
            )
        except Exception as exc:
            if error_code(exc) == "ConditionalCheckFailedException":
                log.info("appointment %s already recorded, dropping duplicate", data["appointment_id"])
                return True
            log.error("audit write failed for appointment %s: %s", data["appointment_id"], type(exc).__name__)
            return False

        log.info("reminder sent for appointment %s", data["appointment_id"])
        return True

    def run_once(self):
        response = self.sqs.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=20
        )
        for message in response.get("Messages", []):
            if not self.handle(message):
                continue
            try:
                self.sqs.delete_message(
                    QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"]
                )
            except Exception as exc:
                log.error("delete failed: %s", type(exc).__name__)


def main():
    import boto3  # imported here so the unit tests do not need boto3

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    region = os.environ["AWS_REGION"]
    worker = Worker(
        sqs=boto3.client("sqs", region_name=region),
        ses=boto3.client("sesv2", region_name=region),
        ddb=boto3.client("dynamodb", region_name=region),
        queue_url=os.environ["QUEUE_URL"],
        sender=os.environ["SENDER_EMAIL"],
        table=os.environ["AUDIT_TABLE"],
    )

    state = {"stop": False}

    def request_stop(signum, frame):
        state["stop"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    log.info("worker started")
    while not state["stop"]:
        try:
            worker.run_once()
        except Exception as exc:
            log.error("poll failed: %s", type(exc).__name__)
            time.sleep(5)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
