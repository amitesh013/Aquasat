"""Read-only REST API used by the local AquaSat Streamlit dashboard."""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Query

from aquasat_database import (
    list_reservoirs,
    read_dashboard,
    read_forecasts,
    read_history,
    sync_csv_outputs,
)


app = FastAPI(
    title="AquaSat API",
    version="1.0.0",
    description=(
        "Read-only API for validated AquaSat dashboard observations, forecasts, "
        "and calibration metrics. CSV processing outputs are synced into SQLite."
    ),
)

RESERVOIR_ALIASES = {
    "redhills": "puzhal",
    "red-hills": "puzhal",
    "puzhal-redhills": "puzhal",
}


def _slug(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-").replace(" ", "-")
    return RESERVOIR_ALIASES.get(normalized, normalized)


def _sync_or_503() -> dict:
    try:
        return sync_csv_outputs()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"AquaSat data sync failed: {exc}") from exc


@app.get("/health", tags=["system"])
def health() -> dict:
    try:
        sync_result = sync_csv_outputs()
        dashboard = read_dashboard()
        return {
            "status": "ok",
            "service": "aquasat-api",
            "database": dashboard["data_status"]["database"],
            "last_synced_at_utc": dashboard["data_status"]["last_synced_at_utc"],
            "sync_updated": sync_result["updated"],
        }
    except (OSError, ValueError) as exc:
        return {"status": "degraded", "service": "aquasat-api", "detail": str(exc)}


@app.get("/api/v1/dashboard", tags=["dashboard"])
def dashboard() -> dict:
    _sync_or_503()
    return read_dashboard()


@app.get("/api/v1/reservoirs", tags=["reservoirs"])
def reservoirs() -> list[dict]:
    _sync_or_503()
    return list_reservoirs()


@app.get("/api/v1/reservoirs/{reservoir}/history", tags=["reservoirs"])
def reservoir_history(
    reservoir: str,
    start_date: date | None = Query(default=None, description="Inclusive start date (YYYY-MM-DD)"),
    end_date: date | None = Query(default=None, description="Inclusive end date (YYYY-MM-DD)"),
) -> list[dict]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    _sync_or_503()
    slug = _slug(reservoir)
    rows = read_history(
        slug,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No observations found for reservoir '{reservoir}'")
    return rows


@app.get("/api/v1/reservoirs/{reservoir}/forecast", tags=["forecasts"])
def reservoir_forecast(
    reservoir: str,
    include_expired: bool = Query(default=False),
) -> list[dict]:
    _sync_or_503()
    slug = _slug(reservoir)
    rows = read_forecasts(slug, include_expired=include_expired)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No forecast found for reservoir '{reservoir}'")
    return rows


@app.get("/api/v1/data-status", tags=["system"])
def data_status() -> dict:
    _sync_or_503()
    return read_dashboard()["data_status"]
