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

The six tenant isolation tests skip unless two connection strings are set. They
need a real PostgreSQL with the migration applied and a separate non-superuser
application role, because row-level security does not apply to superusers.
`docker-compose.test.yml` exposes the database on `127.0.0.1:5433` for this. CI
runs them against a PostgreSQL service container on every pull request.

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
