import json

from worker.worker import Worker


class FakeSqs:
    def __init__(self, messages):
        self.messages = messages
        self.deleted = []

    def receive_message(self, **kwargs):
        return {"Messages": self.messages}

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs["ReceiptHandle"])


class FakeSes:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send_email(self, **kwargs):
        if self.fail:
            raise RuntimeError("ses down")
        self.sent.append(kwargs)


class FakeDdb:
    def __init__(self, error=None):
        self.error = error
        self.items = []

    def put_item(self, **kwargs):
        if self.error:
            raise self.error
        self.items.append(kwargs)


class AlreadyRecorded(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


def message(body=None, receipt="r1"):
    if body is None:
        body = {
            "clinic_id": "clinic-a",
            "appointment_id": "appt-1",
            "recipient": "patient@example.com",
            "starts_at": "2026-12-01T09:00:00Z",
        }
    return {"MessageId": "m1", "ReceiptHandle": receipt, "Body": json.dumps(body)}


def build(messages, ses=None, ddb=None):
    sqs = FakeSqs(messages)
    ses = ses or FakeSes()
    ddb = ddb or FakeDdb()
    return Worker(sqs, ses, ddb, "queue-url", "sender@example.com", "audit"), sqs, ses, ddb


def test_success_sends_records_and_deletes():
    worker, sqs, ses, ddb = build([message()])
    worker.run_once()
    assert len(ses.sent) == 1
    assert len(ddb.items) == 1
    assert sqs.deleted == ["r1"]


def test_send_failure_keeps_message_and_records_nothing():
    worker, sqs, ses, ddb = build([message()], ses=FakeSes(fail=True))
    worker.run_once()
    assert ddb.items == []
    assert sqs.deleted == []


def test_audit_failure_keeps_message():
    worker, sqs, ses, ddb = build([message()], ddb=FakeDdb(error=RuntimeError("ddb down")))
    worker.run_once()
    assert sqs.deleted == []


def test_bad_message_is_kept_for_the_dead_letter_queue_and_sends_nothing():
    worker, sqs, ses, ddb = build([message(body={"clinic_id": "clinic-a"})])
    worker.run_once()
    assert ses.sent == []
    assert sqs.deleted == []


def test_duplicate_delivery_is_dropped():
    worker, sqs, ses, ddb = build([message()], ddb=FakeDdb(error=AlreadyRecorded()))
    worker.run_once()
    assert sqs.deleted == ["r1"]


def test_audit_record_never_holds_the_recipient_address():
    worker, sqs, ses, ddb = build([message()])
    worker.run_once()
    assert "patient@example.com" not in json.dumps(ddb.items)
