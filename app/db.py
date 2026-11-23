"""Database access. Every tenant query runs inside a transaction that first
sets app.clinic_id, so row-level security limits it to one clinic."""
import os
from contextlib import contextmanager

import psycopg
from fastapi import HTTPException, status
from psycopg.rows import dict_row


def connect():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no database configured")
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=5)


@contextmanager
def clinic_session(clinic_id):
    """A transaction acting for one clinic. Commits on success, rolls back on error."""
    with connect() as conn:
        conn.execute("SELECT set_config('app.clinic_id', %s, true)", (str(clinic_id),))
        yield conn
