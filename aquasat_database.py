"""Neon PostgreSQL storage for AquaSat dashboard data."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Index,
    Integer, MetaData, String, Table, Text, create_engine, delete, func, insert,
    inspect, select,
)
from sqlalchemy.engine import Engine
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

RESERVOIRS = {
    "poondi": ("Poondi", 3231),
    "cholavaram": ("Cholavaram", 1081),
    "puzhal": ("Puzhal (Redhills)", 3300),
    "chembarambakkam": ("Chembarambakkam", 3645),
    "veeranam": ("Veeranam", 1465),
}
CORE_FILES = {
    "history": "dashboard_data.csv",
    "forecast": "reservoir_forecasts.csv",
    "metrics": "metrics.csv",
}
REQUIRED_COLUMNS = {
    "history": {"reservoir", "date", "sat_estimate_mcft", "official_mcft"},
    "forecast": {"ds", "reservoir", "yhat", "yhat_lower", "yhat_upper"},
    "metrics": {"reservoir"},
}
NUMBER_COLUMNS = {
    "sat_estimate_mcft", "official_mcft", "yhat", "yhat_lower", "yhat_upper",
    "horizon_days", "day15_prediction_mcft", "satellite_age_days",
    "backtest_rmse_mcft", "backtest_pairs", "valid_up_to_mcft", "rmse", "bias",
    "low_storage_bias", "n_matched", "low_storage_pairs",
    "low_storage_rmse", "rainfall_7d_mm", "rainfall_30d_mm", "rainfall_90d_mm",
}
INTEGER_COLUMNS = {"horizon_days", "backtest_pairs", "n_matched", "low_storage_pairs"}

metadata = MetaData()

reservoir_table = Table(
    "reservoirs", metadata,
    Column("slug", String(64), primary_key=True),
    Column("display_name", String(120), nullable=False),
    Column("capacity_mcft", Integer, nullable=False),
)

observation_table = Table(
    "storage_observations", metadata,
    Column("reservoir", String(64), ForeignKey("reservoirs.slug"), primary_key=True),
    Column("observation_date", Date, primary_key=True),
    Column("source", String(32), primary_key=True),
    Column("storage_mcft", Float),
    Column("metadata_json", Text),
)
Index("ix_storage_observations_date_source", observation_table.c.observation_date, observation_table.c.source)

forecast_table = Table(
    "forecasts", metadata,
    Column("reservoir", String(64), ForeignKey("reservoirs.slug"), primary_key=True),
    Column("forecast_date", Date, primary_key=True),
    Column("model", String(100), primary_key=True),
    Column("origin_date", Date),
    Column("horizon_days", Integer),
    Column("yhat", Float),
    Column("yhat_lower", Float),
    Column("yhat_upper", Float),
    Column("generated_at_utc", String(40)),
    Column("payload_json", Text, nullable=False),
)
Index("ix_forecasts_date", forecast_table.c.forecast_date)

metric_table = Table(
    "calibration_metrics", metadata,
    Column("reservoir", String(64), ForeignKey("reservoirs.slug"), primary_key=True),
    Column("payload_json", Text, nullable=False),
)

source_sync_table = Table(
    "source_sync", metadata,
    Column("source_file", String(120), primary_key=True),
    Column("sha256", String(64), nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("synced_at_utc", String(40), nullable=False),
)

auth_user_table = Table(
    "auth_users", metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(30), nullable=False, unique=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("is_active", Boolean, nullable=False, server_default="true"),
)

auth_session_table = Table(
    "auth_sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)
Index("ix_auth_sessions_user_id", auth_session_table.c.user_id)
Index("ix_auth_sessions_expires_at", auth_session_table.c.expires_at)


def _normalize_database_url(url: str | None = None) -> str:
    configured = (url or os.environ.get("DATABASE_URL") or "").strip()
    if not configured:
        raise RuntimeError(
            "DATABASE_URL is required. Set the Neon PostgreSQL connection string "
            "in the API environment."
        )
    if configured.startswith("postgres://"):
        configured = "postgresql+psycopg://" + configured[len("postgres://"):]
    elif configured.startswith("postgresql://"):
        configured = "postgresql+psycopg://" + configured[len("postgresql://"):]
    if not configured.startswith("postgresql+psycopg://"):
        raise RuntimeError(
            "DATABASE_URL must use PostgreSQL with the psycopg driver. "
            "Use the Neon PostgreSQL connection string."
        )
    return configured


@lru_cache(maxsize=4)
def _engine_for(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def get_engine(database_url: str | None = None) -> Engine:
    return _engine_for(_normalize_database_url(database_url))


def database_backend(database_url: str | None = None) -> str:
    _normalize_database_url(database_url)
    return "Neon PostgreSQL"


def initialize_database(database_url: str | None = None) -> None:
    engine = get_engine(database_url)
    if not inspect(engine).has_table("reservoirs"):
        raise RuntimeError(
            "The Neon schema is not initialized. Run 'alembic upgrade head' before "
            "starting AquaSat."
        )
    with engine.begin() as connection:
        for slug, (display_name, capacity_mcft) in RESERVOIRS.items():
            exists = connection.execute(
                select(reservoir_table.c.slug).where(reservoir_table.c.slug == slug)
            ).first()
            if exists is None:
                connection.execute(insert(reservoir_table).values(
                    slug=slug, display_name=display_name, capacity_mcft=capacity_mcft
                ))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, dataset: str) -> list[dict[str, str]]:
    if not path.is_file():
        if dataset == "metrics":
            return []
        raise FileNotFoundError(f"Required AquaSat input is missing: {path.name}")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS[dataset] - columns
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(sorted(missing))}")
        return [dict(row) for row in reader]


def _clean_number(value: Any, column: str) -> int | float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected a number in column {column}; found {value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if column in INTEGER_COLUMNS else number


def _iso_date(value: Any, filename: str, column: str) -> date:
    raw = str(value or "").strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ValueError(f"Invalid {column} value {raw!r} in {filename}") from exc


def _clean_record(record: dict[str, str]) -> dict[str, Any]:
    return {
        key: _clean_number(value, key) if key in NUMBER_COLUMNS
        else (value if value not in (None, "") else None)
        for key, value in record.items()
    }


def _source_status(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        select(source_sync_table).order_by(source_sync_table.c.source_file)
    ).mappings()
    return [dict(row) for row in rows]


def sync_csv_outputs(database_url: str | None = None, force: bool = False) -> dict[str, Any]:
    """Validate the current output CSVs and atomically replace their DB snapshot."""
    initialize_database(database_url)
    engine = get_engine(database_url)
    paths = {name: PROJECT_DIR / filename for name, filename in CORE_FILES.items()}
    fingerprints = {name: (_sha256(path) if path.is_file() else None) for name, path in paths.items()}

    with engine.connect() as connection:
        old = {
            row["source_file"]: row["sha256"]
            for row in connection.execute(select(source_sync_table)).mappings()
        }
        expected = {path.name for path in paths.values() if path.exists()}
        if not force and expected == set(old) and all(
            old.get(path.name) == fingerprints[name]
            for name, path in paths.items() if path.exists()
        ):
            return {
                "updated": False,
                "backend": database_backend(database_url),
                "sources": _source_status(connection),
            }

    history_rows = _read_csv(paths["history"], "history")
    forecast_rows = _read_csv(paths["forecast"], "forecast")
    metric_rows = _read_csv(paths["metrics"], "metrics")

    observations: dict[tuple[str, date, str], dict[str, Any]] = {}
    for row in history_rows:
        lake = (row.get("reservoir") or "").strip().lower()
        if lake not in RESERVOIRS:
            raise ValueError(f"Unknown reservoir in dashboard_data.csv: {lake!r}")
        observed_on = _iso_date(row.get("date"), "dashboard_data.csv", "date")
        for source_name, column in (("cmwssb", "official_mcft"), ("sentinel2", "sat_estimate_mcft")):
            value = _clean_number(row.get(column), column)
            if value is not None:
                observations[(lake, observed_on, source_name)] = {
                    "reservoir": lake,
                    "observation_date": observed_on,
                    "source": source_name,
                    "storage_mcft": value,
                    "metadata_json": None,
                }

    forecasts: dict[tuple[str, date, str], dict[str, Any]] = {}
    for row in forecast_rows:
        lake = (row.get("reservoir") or "").strip().lower()
        if lake not in RESERVOIRS:
            raise ValueError(f"Unknown reservoir in reservoir_forecasts.csv: {lake!r}")
        forecast_on = _iso_date(row.get("ds"), "reservoir_forecasts.csv", "ds")
        cleaned = _clean_record(row)
        model = str(cleaned.get("model") or "unknown")
        cleaned["reservoir"] = lake
        cleaned["ds"] = forecast_on.isoformat()
        origin = cleaned.get("forecast_origin")
        forecasts[(lake, forecast_on, model)] = {
            "reservoir": lake,
            "forecast_date": forecast_on,
            "model": model,
            "origin_date": _iso_date(origin, "reservoir_forecasts.csv", "forecast_origin") if origin else None,
            "horizon_days": cleaned.get("horizon_days"),
            "yhat": cleaned.get("yhat"),
            "yhat_lower": cleaned.get("yhat_lower"),
            "yhat_upper": cleaned.get("yhat_upper"),
            "generated_at_utc": cleaned.get("generated_at_utc"),
            "payload_json": json.dumps(cleaned, allow_nan=False, separators=(",", ":")),
        }

    metrics: dict[str, dict[str, str]] = {}
    for row in metric_rows:
        lake = (row.get("reservoir") or "").strip().lower()
        if lake in RESERVOIRS:
            cleaned = _clean_record(row)
            cleaned["reservoir"] = lake
            metrics[lake] = {"reservoir": lake, "payload_json": json.dumps(cleaned, allow_nan=False, separators=(",", ":"))}

    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row_counts = {
        "dashboard_data.csv": len(observations),
        "reservoir_forecasts.csv": len(forecasts),
        "metrics.csv": len(metrics),
    }
    with engine.begin() as connection:
        connection.execute(delete(observation_table))
        connection.execute(delete(forecast_table))
        connection.execute(delete(metric_table))
        connection.execute(delete(source_sync_table))
        if observations:
            connection.execute(insert(observation_table), list(observations.values()))
        if forecasts:
            connection.execute(insert(forecast_table), list(forecasts.values()))
        if metrics:
            connection.execute(insert(metric_table), list(metrics.values()))
        sync_rows = [
            {
                "source_file": path.name,
                "sha256": fingerprints[name],
                "row_count": row_counts[path.name],
                "synced_at_utc": synced_at,
            }
            for name, path in paths.items() if path.is_file()
        ]
        if sync_rows:
            connection.execute(insert(source_sync_table), sync_rows)

    return {
        "updated": True,
        "backend": database_backend(database_url),
        "row_counts": row_counts,
        "synced_at_utc": synced_at,
    }


def _history_records(connection, lake: str | None = None,
                     start_date: date | None = None, end_date: date | None = None) -> list[dict[str, Any]]:
    query = select(observation_table).order_by(
        observation_table.c.observation_date, observation_table.c.reservoir
    )
    if lake:
        query = query.where(observation_table.c.reservoir == lake)
    if start_date:
        query = query.where(observation_table.c.observation_date >= start_date)
    if end_date:
        query = query.where(observation_table.c.observation_date <= end_date)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in connection.execute(query).mappings():
        date_text = row["observation_date"].isoformat()
        entry = result.setdefault((row["reservoir"], date_text), {
            "reservoir": row["reservoir"], "date": date_text,
            "official_mcft": None, "sat_estimate_mcft": None,
        })
        if row["source"] == "cmwssb":
            entry["official_mcft"] = row["storage_mcft"]
        elif row["source"] == "sentinel2":
            entry["sat_estimate_mcft"] = row["storage_mcft"]
    return list(result.values())


def read_dashboard(database_url: str | None = None) -> dict[str, Any]:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        history = _history_records(connection)
        forecasts = [
            json.loads(row["payload_json"])
            for row in connection.execute(select(forecast_table).order_by(
                forecast_table.c.forecast_date, forecast_table.c.reservoir
            )).mappings()
        ]
        metrics = {
            row["reservoir"]: json.loads(row["payload_json"])
            for row in connection.execute(select(metric_table)).mappings()
        }
        sources = _source_status(connection)
    return {
        "history": history,
        "forecasts": forecasts,
        "metrics": metrics,
        "data_status": {
            "database": database_backend(database_url),
            "last_synced_at_utc": max((s["synced_at_utc"] for s in sources), default=None),
            "sources": sources,
        },
    }


def read_history(reservoir: str, start_date: date | None = None,
                 end_date: date | None = None, database_url: str | None = None) -> list[dict[str, Any]]:
    with get_engine(database_url).connect() as connection:
        return _history_records(connection, reservoir, start_date, end_date)


def read_forecasts(reservoir: str, include_expired: bool = False,
                   database_url: str | None = None) -> list[dict[str, Any]]:
    query = select(forecast_table.c.payload_json).where(
        forecast_table.c.reservoir == reservoir
    ).order_by(forecast_table.c.forecast_date)
    if not include_expired:
        query = query.where(forecast_table.c.forecast_date >= date.today())
    with get_engine(database_url).connect() as connection:
        return [json.loads(row[0]) for row in connection.execute(query)]


def list_reservoirs(database_url: str | None = None) -> list[dict[str, Any]]:
    latest = select(
        observation_table.c.reservoir,
        func.max(observation_table.c.observation_date).label("latest_observation"),
    ).group_by(observation_table.c.reservoir).subquery()
    query = select(
        reservoir_table.c.slug,
        reservoir_table.c.display_name,
        reservoir_table.c.capacity_mcft,
        latest.c.latest_observation,
    ).outerjoin(latest, latest.c.reservoir == reservoir_table.c.slug).order_by(
        reservoir_table.c.display_name
    )
    with get_engine(database_url).connect() as connection:
        return [
            {
                **dict(row),
                "latest_observation": row["latest_observation"].isoformat()
                if row["latest_observation"] else None,
            }
            for row in connection.execute(query).mappings()
        ]
