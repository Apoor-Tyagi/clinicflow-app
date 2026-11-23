import os
import random
import time

import psycopg
from fastapi import FastAPI, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.api import router as api_router

app = FastAPI(title="ClinicFlow API")
app.include_router(api_router)

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["path"])

# Fault injection for alert testing. 0 (the default) leaves behaviour unchanged.
FAULT_RATE = float(os.getenv("FAULT_RATE", "0"))


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    # Label by route template, not the raw URL, so ids in the path cannot create
    # unbounded metric series. Requests that match no route share one label.
    route = request.scope.get("route")
    path = route.path if route is not None else "unmatched"
    LATENCY.labels(path).observe(time.perf_counter() - start)
    REQUESTS.labels(request.method, path, response.status_code).inc()
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready(response: Response):
    url = os.getenv("DATABASE_URL")
    if not url:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "no database configured"}
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database unreachable"}
    return {"status": "ready"}


@app.get("/api/v1/ping")
def ping():
    if FAULT_RATE > 0 and random.random() < FAULT_RATE:
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return {"message": "pong"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
