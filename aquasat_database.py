"""SQLite snapshot store for AquaSat's validated dashboard CSV outputs.

The existing processing scripts continue to produce CSV files. This module
validates those files and atomically syncs the dashboard history, forecast,
and metrics into SQLite so the Streamlit app and REST API can share one store.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AQUASAT_DB_PATH", PROJECT_DIR / "aquasat.sqlite3"))
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
    "low_storage_bias", "n_matched", "cv_splits", "low_storage_pairs",
    "low_storage_rmse",
}
INTEGER_COLUMNS = {"horizon_days", "backtest_pairs", "n_matched", "cv_splits", "low_storage_pairs"}


def database_path() -> Path:
    """Return the configured database path, resolving relative overrides locally."""
    configured = Path(os.environ.get("AQUASAT_DB_PATH", DB_PATH))
    if not configured.is_absolute():
        configured = PROJECT_DIR / configured
    return configured


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def initialize_database(db_path: str | Path | None = None) -> None:
    with connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS observations (
                reservoir TEXT NOT NULL,
                observation_date TEXT NOT NULL,
                sat_estimate_mcft REAL,
                official_mcft REAL,
                PRIMARY KEY (reservoir, observation_date)
            );
            CREATE INDEX IF NOT EXISTS idx_observations_reservoir_date
                ON observations (reservoir, observation_date);

            CREATE TABLE IF NOT EXISTS forecasts (
                reservoir TEXT NOT NULL,
                forecast_date TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (reservoir, forecast_date)
            );
            CREATE INDEX IF NOT EXISTS idx_forecasts_reservoir_date
                ON forecasts (reservoir, forecast_date);

            CREATE TABLE IF NOT EXISTS metrics (
                reservoir TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_sync (
                source_file TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                synced_at_utc TEXT NOT NULL
            );
            """
        )


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
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if column in INTEGER_COLUMNS else number


def _iso_date(value: Any, filename: str, column: str) -> str:
    raw = str(value or "").strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid {column} value {raw!r} in {filename}") from exc


def _clean_record(record: dict[str, str]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in record.items():
        if key in NUMBER_COLUMNS:
            cleaned[key] = _clean_number(value, key)
        else:
            cleaned[key] = value if value not in (None, "") else None
    return cleaned


def sync_csv_outputs(db_path: str | Path | None = None, force: bool = False) -> dict[str, Any]:
    """Validate and sync the core CSV outputs as one SQLite transaction.

    CSVs remain the pipeline's source artifacts during this migration. A bad
    or incomplete input never partially replaces the last good database copy.
    """
    initialize_database(db_path)
    paths = {name: PROJECT_DIR / filename for name, filename in CORE_FILES.items()}
    fingerprints = {
        name: (_sha256(path) if path.is_file() else None)
        for name, path in paths.items()
    }

    with connect(db_path) as connection:
        old = {
            row["source_file"]: row["sha256"]
            for row in connection.execute("SELECT source_file, sha256 FROM source_sync")
        }
        expected_names = {path.name for path in paths.values() if path.exists()}
        if not force and expected_names == set(old) and all(
            old.get(path.name) == fingerprints[name]
            for name, path in paths.items()
            if path.exists()
        ):
            return {
                "updated": False,
                "database": str(database_path() if db_path is None else Path(db_path)),
                "sources": _source_status(connection),
            }

    # Parse and validate before opening the replacement transaction.
    history_rows = _read_csv(paths["history"], "history")
    forecast_rows = _read_csv(paths["forecast"], "forecast")
    metric_rows = _read_csv(paths["metrics"], "metrics")

    observations: dict[tuple[str, str], tuple[str, str, float | None, float | None]] = {}
    for row in history_rows:
        reservoir = (row.get("reservoir") or "").strip().lower()
        if not reservoir:
            raise ValueError("dashboard_data.csv contains a blank reservoir name")
        date = _iso_date(row.get("date"), "dashboard_data.csv", "date")
        observations[(reservoir, date)] = (
            reservoir, date,
            _clean_number(row.get("sat_estimate_mcft"), "sat_estimate_mcft"),
            _clean_number(row.get("official_mcft"), "official_mcft"),
        )

    forecasts: dict[tuple[str, str], tuple[str, str, str]] = {}
    for row in forecast_rows:
        reservoir = (row.get("reservoir") or "").strip().lower()
        if not reservoir:
            raise ValueError("reservoir_forecasts.csv contains a blank reservoir name")
        forecast_date = _iso_date(row.get("ds"), "reservoir_forecasts.csv", "ds")
        cleaned = _clean_record(row)
        cleaned["reservoir"] = reservoir
        cleaned["ds"] = forecast_date
        forecasts[(reservoir, forecast_date)] = (
            reservoir, forecast_date,
            json.dumps(cleaned, allow_nan=False, separators=(",", ":")),
        )

    metrics: dict[str, str] = {}
    for row in metric_rows:
        reservoir = (row.get("reservoir") or "").strip().lower()
        if not reservoir:
            continue
        cleaned = _clean_record(row)
        cleaned["reservoir"] = reservoir
        metrics[reservoir] = json.dumps(cleaned, allow_nan=False, separators=(",", ":"))

    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row_counts = {
        "dashboard_data.csv": len(observations),
        "reservoir_forecasts.csv": len(forecasts),
        "metrics.csv": len(metrics),
    }
    with connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM observations")
        connection.execute("DELETE FROM forecasts")
        connection.execute("DELETE FROM metrics")
        connection.executemany(
            "INSERT INTO observations VALUES (?, ?, ?, ?)", observations.values()
        )
        connection.executemany(
            "INSERT INTO forecasts VALUES (?, ?, ?)", forecasts.values()
        )
        connection.executemany(
            "INSERT INTO metrics VALUES (?, ?)", metrics.items()
        )
        for name, path in paths.items():
            if path.exists():
                connection.execute(
                    "INSERT INTO source_sync (source_file, sha256, row_count, synced_at_utc) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(source_file) DO UPDATE SET "
                    "sha256=excluded.sha256, row_count=excluded.row_count, synced_at_utc=excluded.synced_at_utc",
                    (path.name, fingerprints[name], row_counts[path.name], synced_at),
                )
            else:
                connection.execute("DELETE FROM source_sync WHERE source_file = ?", (path.name,))
        connection.commit()

    return {
        "updated": True,
        "database": str(database_path() if db_path is None else Path(db_path)),
        "row_counts": row_counts,
        "synced_at_utc": synced_at,
    }


def _source_status(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(
        "SELECT source_file, row_count, synced_at_utc FROM source_sync ORDER BY source_file"
    )]


def read_dashboard(db_path: str | Path | None = None) -> dict[str, Any]:
    with connect(db_path) as connection:
        history = [dict(row) for row in connection.execute(
            "SELECT reservoir, observation_date AS date, sat_estimate_mcft, official_mcft "
            "FROM observations ORDER BY observation_date, reservoir"
        )]
        forecasts = [
            json.loads(row["payload_json"])
            for row in connection.execute(
                "SELECT payload_json FROM forecasts ORDER BY forecast_date, reservoir"
            )
        ]
        metrics = {
            row["reservoir"]: json.loads(row["payload_json"])
            for row in connection.execute("SELECT reservoir, payload_json FROM metrics")
        }
        sources = _source_status(connection)
    return {
        "history": history,
        "forecasts": forecasts,
        "metrics": metrics,
        "data_status": {
            "database": str(database_path() if db_path is None else Path(db_path)),
            "last_synced_at_utc": max(
                (source["synced_at_utc"] for source in sources), default=None
            ),
            "sources": sources,
        },
    }


def read_history(reservoir: str, start_date: str | None = None, end_date: str | None = None,
                 db_path: str | Path | None = None) -> list[dict[str, Any]]:
    query = (
        "SELECT reservoir, observation_date AS date, sat_estimate_mcft, official_mcft "
        "FROM observations WHERE reservoir = ?"
    )
    params: list[Any] = [reservoir]
    if start_date:
        query += " AND observation_date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND observation_date <= ?"
        params.append(end_date)
    query += " ORDER BY observation_date"
    with connect(db_path) as connection:
        return [dict(row) for row in connection.execute(query, params)]


def read_forecasts(reservoir: str, include_expired: bool = False,
                   db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT payload_json FROM forecasts WHERE reservoir = ? ORDER BY forecast_date",
            (reservoir,),
        )
        forecasts = [json.loads(row["payload_json"]) for row in rows]
    if not include_expired:
        today = datetime.now().date().isoformat()
        forecasts = [row for row in forecasts if (row.get("ds") or "") >= today]
    return forecasts


def list_reservoirs(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT reservoir, MAX(observation_date) AS latest_observation "
            "FROM observations GROUP BY reservoir ORDER BY reservoir"
        )
        return [dict(row) for row in rows]
