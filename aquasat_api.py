"""Authenticated AquaSat dashboard API and static website."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from aquasat_database import (
    auth_session_table,
    auth_user_table,
    database_backend,
    get_engine,
    list_reservoirs,
    read_dashboard,
    read_forecasts,
    read_history,
)


AUTH_COOKIE_LOCAL = "aquasat_session"
AUTH_COOKIE_HTTPS = "__Host-aquasat_session"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)
_DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,28}[a-z0-9]$")
_AUTH_LOGGER = logging.getLogger("aquasat.auth")


def _cookie_secure() -> bool:
    return os.environ.get("AQUASAT_COOKIE_SECURE", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _cookie_name() -> str:
    return AUTH_COOKIE_HTTPS if _cookie_secure() else AUTH_COOKIE_LOCAL


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _set_session_cookie(response: Response, token: str) -> None:
    secure = _cookie_secure()
    response.set_cookie(
        _cookie_name(),
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(_cookie_name(), path="/", httponly=True, secure=_cookie_secure(), samesite="lax")
    # Remove sessions created by the earlier single-admin prototype.
    response.delete_cookie(AUTH_COOKIE_LOCAL, path="/", httponly=True, samesite="lax")
    response.delete_cookie(AUTH_COOKIE_HTTPS, path="/", httponly=True, secure=True, samesite="lax")


def _store_session(connection, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    connection.execute(delete(auth_session_table).where(auth_session_table.c.expires_at <= now))
    token = secrets.token_urlsafe(32)
    connection.execute(insert(auth_session_table).values(
        token_hash=_token_hash(token),
        user_id=user_id,
        expires_at=now + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
    ))
    return token


def _session_user(request: Request) -> dict | None:
    token = request.cookies.get(_cookie_name())
    if not token:
        return None
    query = (
        select(auth_user_table.c.id, auth_user_table.c.email, auth_user_table.c.username)
        .select_from(auth_session_table.join(
            auth_user_table,
            auth_session_table.c.user_id == auth_user_table.c.id,
        ))
        .where(
            auth_session_table.c.token_hash == _token_hash(token),
            auth_session_table.c.expires_at > func.now(),
            auth_user_table.c.is_active.is_(True),
        )
    )
    with get_engine().connect() as connection:
        row = connection.execute(query).mappings().first()
    return dict(row) if row else None


def _require_login(request: Request) -> None:
    if _session_user(request) is None:
        raise HTTPException(status_code=401, detail="Please sign in to view AquaSat data.")


def _normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if not _EMAIL_RE.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return normalized


def _normalize_username(username: str) -> str:
    normalized = username.strip().casefold()
    if not _USERNAME_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3–30 characters, start and end with a letter or number, and use only letters, numbers, dots, underscores, or hyphens.",
        )
    return normalized


def _verify_password(stored_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(stored_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=1, max_length=128)


class RegistrationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=6, max_length=128)


app = FastAPI(
    title="AquaSat API",
    version="1.0.0",
    description=(
        "Read-only API for AquaSat dashboard observations, forecasts, calibration "
        "metrics, and saved research results. Reviewed CSV outputs are synced into the "
        "Neon PostgreSQL database."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get(
            "AQUASAT_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)

RESERVOIR_ALIASES = {
    "redhills": "puzhal",
    "red-hills": "puzhal",
    "puzhal-redhills": "puzhal",
}


def _slug(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-").replace(" ", "-")
    return RESERVOIR_ALIASES.get(normalized, normalized)


@app.get("/health", tags=["system"])
def health() -> dict:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "service": "aquasat-api",
            "database": database_backend(),
        }
    except Exception:
        return {
            "status": "degraded",
            "service": "aquasat-api",
            "detail": "Neon is unavailable or the database URL is not configured.",
        }


@app.get("/api/v1/auth/status", tags=["auth"])
def auth_status(request: Request, response: Response) -> dict:
    _no_store(response)
    user = _session_user(request)
    if user is None:
        _clear_session_cookies(response)
    return {
        "required": True,
        "configured": True,
        "authenticated": user is not None,
        "email": user["email"] if user else None,
        "username": user["username"] if user else None,
    }


@app.post("/api/v1/auth/register", status_code=201, tags=["auth"])
def register(credentials: RegistrationRequest, response: Response) -> dict:
    username = _normalize_username(credentials.username)
    email = _normalize_email(credentials.email)
    password_hash = PASSWORD_HASHER.hash(credentials.password)
    user_id = uuid.uuid4().hex
    try:
        with get_engine().begin() as connection:
            connection.execute(insert(auth_user_table).values(
                id=user_id,
                username=username,
                email=email,
                password_hash=password_hash,
            ))
            token = _store_session(connection, user_id)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="An account with that username or email already exists. Sign in instead or choose another.",
        ) from exc

    _set_session_cookie(response, token)
    _no_store(response)
    return {"authenticated": True, "email": email, "username": username}


@app.post("/api/v1/auth/login", tags=["auth"])
def login(credentials: LoginRequest, response: Response) -> dict:
    username = credentials.username.strip().casefold()
    if not _USERNAME_RE.fullmatch(username):
        _verify_password(_DUMMY_PASSWORD_HASH, credentials.password)
        raise HTTPException(status_code=401, detail="Username or password is incorrect.")
    lookup = auth_user_table.c.username == username

    with get_engine().begin() as connection:
        user = connection.execute(select(auth_user_table).where(lookup)).mappings().first()
        if user is None:
            _verify_password(_DUMMY_PASSWORD_HASH, credentials.password)
            raise HTTPException(status_code=401, detail="Username or password is incorrect.")
        if not _verify_password(user["password_hash"], credentials.password) or not user["is_active"]:
            raise HTTPException(status_code=401, detail="Username or password is incorrect.")

        if PASSWORD_HASHER.check_needs_rehash(user["password_hash"]):
            connection.execute(
                update(auth_user_table)
                .where(auth_user_table.c.id == user["id"])
                .values(password_hash=PASSWORD_HASHER.hash(credentials.password))
            )
        token = _store_session(connection, user["id"])

    _set_session_cookie(response, token)
    _no_store(response)
    return {"authenticated": True, "email": user["email"], "username": user["username"]}


@app.post("/api/v1/auth/logout", tags=["auth"])
def logout(request: Request, response: Response) -> dict:
    _no_store(response)
    token = request.cookies.get(_cookie_name())
    _clear_session_cookies(response)
    if token:
        try:
            with get_engine().begin() as connection:
                connection.execute(
                    delete(auth_session_table).where(
                        auth_session_table.c.token_hash == _token_hash(token)
                    )
                )
        except Exception:
            _AUTH_LOGGER.exception("Could not revoke the signed-out account session.")
    return {"authenticated": False}


@app.get("/api/v1/dashboard", tags=["dashboard"], dependencies=[Depends(_require_login)])
def dashboard() -> dict:
    return read_dashboard()


@app.get("/api/v1/reservoirs", tags=["reservoirs"], dependencies=[Depends(_require_login)])
def reservoirs() -> list[dict]:
    return list_reservoirs()


@app.get("/api/v1/reservoirs/{reservoir}/history", tags=["reservoirs"], dependencies=[Depends(_require_login)])
def reservoir_history(
    reservoir: str,
    start_date: date | None = Query(default=None, description="Inclusive start date (YYYY-MM-DD)"),
    end_date: date | None = Query(default=None, description="Inclusive end date (YYYY-MM-DD)"),
) -> list[dict]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    slug = _slug(reservoir)
    rows = read_history(
        slug,
        start_date,
        end_date,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No observations found for reservoir '{reservoir}'")
    return rows


@app.get("/api/v1/reservoirs/{reservoir}/forecast", tags=["forecasts"], dependencies=[Depends(_require_login)])
def reservoir_forecast(
    reservoir: str,
    include_expired: bool = Query(default=False),
) -> list[dict]:
    slug = _slug(reservoir)
    rows = read_forecasts(slug, include_expired=include_expired)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No forecast found for reservoir '{reservoir}'")
    return rows


@app.get("/api/v1/data-status", tags=["system"], dependencies=[Depends(_require_login)])
def data_status() -> dict:
    return read_dashboard()["data_status"]


@app.get("/api/v1/research/rainfall-candidate", tags=["research"], dependencies=[Depends(_require_login)])
def rainfall_candidate() -> dict:
    """Serve the reviewed GPM rainfall comparison separately from live forecasts."""
    path = Path(__file__).resolve().parent / "rainfall_gpm_candidate_result.json"
    try:
        with path.open("r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Saved rainfall candidate results are unavailable.",
        ) from exc


class SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", SpaStaticFiles(directory=frontend_dist, html=True), name="frontend")
