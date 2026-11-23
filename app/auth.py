"""Authentication. Each clinic has an API key; only its SHA-256 hash is stored.
Keys are random 256-bit values, so a fast hash is enough (no password stretching)."""
import hashlib
import hmac
import os
import secrets

from fastapi import Header, HTTPException, status

from app.db import connect


def new_api_key():
    return "cf_" + secrets.token_urlsafe(32)


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def require_admin(x_admin_token: str | None = Header(default=None)):
    expected = os.getenv("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "admin API is disabled")
    if not x_admin_token or not hmac.compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin token")


def current_clinic(x_api_key: str | None = Header(default=None)):
    """Return the id of the clinic that owns the API key."""
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-API-Key")
    with connect() as conn:
        row = conn.execute(
            "SELECT authenticate_clinic(%s) AS clinic_id", (hash_key(x_api_key),)
        ).fetchone()
    if row["clinic_id"] is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    return row["clinic_id"]
