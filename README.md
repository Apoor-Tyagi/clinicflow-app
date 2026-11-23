# clinicflow-app

The API, the reminder worker and the container image for
[ClinicFlow](https://github.com/Apoor-Tyagi/clinicflow-infra), a multi-tenant
appointment and reminder platform for small clinics. The project overview,
architecture, measured results and decision records live in the infra repository.

## Contents

| Path | What it is |
|---|---|
| `app/` | FastAPI service: `/health`, `/ready`, `/metrics`, `/api/v1/ping` |
| `db/migrations/` | PostgreSQL schema with row-level security per clinic |
| `worker/` | Reminder worker: reads SQS, sends through SES, records to DynamoDB |
| `tests/` | API tests and tenant isolation tests |
| `Dockerfile` | Multi-stage build, non-root user, pip removed from the runtime image |
| `docs/adr/` | Decisions about the container build and image publishing |

The booking endpoints are out of scope. The API serves health checks and metrics,
and reminders are exercised through the queue directly.

## Run it locally

Start the database and the API:

```bash
cp .env.example .env     # then edit the values
docker compose up --build
curl localhost:8000/health
```

Run the tests:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

The tenant isolation, API and dispatcher tests skip unless two connection strings are set. They
need a real PostgreSQL with the migration applied and a separate non-superuser
application role, because row-level security does not apply to superusers.
`docker-compose.test.yml` exposes the database on `127.0.0.1:5433` for this. CI
runs them against a PostgreSQL service container on every pull request.

## API

The API needs `DATABASE_URL` (the `clinicflow_app` role) and `ADMIN_TOKEN`. Create a
clinic once with the admin token, then use the key it returns:

```bash
curl -X POST localhost:8000/api/v1/admin/clinics -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"name": "Demo Clinic"}'
```

Every other call sends `X-API-Key`. Interactive docs are at `/docs`. Booking an
appointment for a patient with an email address queues a reminder 24 hours ahead
in the `reminders` table. Turning queued reminders into scheduled emails is a
separate service, the dispatcher in `dispatcher/`. It runs on the node, where it
can create EventBridge schedules, and it connects as its own database role,
`clinicflow_dispatcher`, which can read reminders across clinics but cannot read
patient names or change anything else. A schedule carries the recipient address
for the worker, so schedules are as sensitive as the queue.

## Pipeline

On every pull request: tests, then an image build and a Trivy scan that fails on
fixable high or critical findings. On `main` only, a multi-architecture image is
pushed to ECR, tagged with the commit SHA, through GitHub OIDC with no stored keys.

## Fault injection

`FAULT_RATE` (default `0`) makes `/api/v1/ping` return 500 for that fraction of
requests. It exists to prove the SLO alerts fire, and must stay unset in normal
operation.

## Worker

`worker/worker.py` runs as a hardened systemd service on the k3s node, using the
node role, not as a pod. The reasoning is in
[ADR 0012](https://github.com/Apoor-Tyagi/clinicflow-infra/blob/main/docs/adr/0012-reminder-messaging.md).
It never logs recipient addresses, and deletes a message only after the email was
sent and the delivery was recorded.
