"""Build a separate daily rainfall candidate from IMD/NCMRWF's merged GPM grid.

The site serves one 241 x 281 binary grid per selected date. This script reads
the nearest grid cell to each reservoir centre and writes the same core CSV
columns used by the forecast research candidates. It never alters the standard
IMD rainfall series or live forecast files.
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


DOWNLOAD_URL = "https://imdpune.gov.in/cmpg/Realtimedata/gpm/rain.php"
SOURCE_PAGE = "https://imdpune.gov.in/cmpg/Realtimedata/gpm/Rain_Download.html"
SOURCE_NAME = "IMD/NCMRWF daily merged gauge + GPM rainfall, 0.25 degree"

GRID_NX = 241
GRID_NY = 281
GRID_LON0 = 50.0
GRID_LAT0 = -30.0
GRID_STEP = 0.25
GRID_UNDEFINED = -999.0
GRID_BYTES = GRID_NX * GRID_NY * 4

# Representative lake-centre coordinates. These are local rainfall proxies,
# not catchment boundaries or basin-average rainfall.
RESERVOIR_CENTRES = {
    "poondi": (13.1823, 79.8567),
    "cholavaram": (13.22804, 80.15193),
    "puzhal": (13.16496, 80.16315),
    "chembarambakkam": (13.00793, 80.05078),
    "veeranam": (11.34454, 79.52858),
}


def _great_circle_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    radius_km = 6371.0088
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    haversine = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(min(1.0, math.sqrt(haversine)))


def extract_daily_payload(day: pd.Timestamp, payload: bytes) -> pd.DataFrame:
    """Decode one little-endian float32 grid and extract the five site cells."""
    day = pd.Timestamp(day).normalize()
    if len(payload) != GRID_BYTES:
        preview = payload[:80].decode("ascii", errors="replace").replace("\n", " ")
        raise ValueError(
            f"{day:%Y-%m-%d}: expected {GRID_BYTES:,} binary bytes, got "
            f"{len(payload):,}; response starts {preview!r}."
        )

    # IMD's sample control file declares XDEF 241, YDEF 281, with longitude
    # changing fastest. The daily file has exactly 241*281 four-byte values.
    grid = np.frombuffer(payload, dtype="<f4").reshape((GRID_NY, GRID_NX))
    records: list[dict[str, object]] = []
    for reservoir, (site_lat, site_lon) in RESERVOIR_CENTRES.items():
        x_index = int(round((site_lon - GRID_LON0) / GRID_STEP))
        y_index = int(round((site_lat - GRID_LAT0) / GRID_STEP))
        if not (0 <= x_index < GRID_NX and 0 <= y_index < GRID_NY):
            raise ValueError(f"{reservoir} falls outside the published IMD grid.")

        amount = float(grid[y_index, x_index])
        if not math.isfinite(amount) or math.isclose(amount, GRID_UNDEFINED) or amount < 0:
            amount = float("nan")
        grid_lat = GRID_LAT0 + y_index * GRID_STEP
        grid_lon = GRID_LON0 + x_index * GRID_STEP
        records.append({
            "date": day,
            "reservoir": reservoir,
            "rainfall_mm": amount,
            "site_latitude": site_lat,
            "site_longitude": site_lon,
            "grid_latitude": grid_lat,
            "grid_longitude": grid_lon,
            "grid_offset_km": _great_circle_km(site_lat, site_lon, grid_lat, grid_lon),
            "rainfall_units": "mm per 24 hours",
            "source_dataset": SOURCE_NAME,
            "source_url": SOURCE_PAGE,
            "spatial_support": "nearest 0.25-degree grid cell to reservoir centre; not catchment mean",
        })
    return pd.DataFrame(records)


def extract_daily_file(path: str | Path, day: pd.Timestamp) -> pd.DataFrame:
    """Decode a previously downloaded daily grid (also useful for audits)."""
    return extract_daily_payload(pd.Timestamp(day), Path(path).read_bytes())


def download_daily_payload(
    day: pd.Timestamp, timeout: float = 60.0, retries: int = 2
) -> bytes:
    """Request one daily grid using the same form field as IMD's download page."""
    day = pd.Timestamp(day).normalize()
    body = urlencode({"rain": day.strftime("%d%m%Y")}).encode("ascii")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = Request(
                DOWNLOAD_URL,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "AquaSat research rainfall candidate",
                },
                method="POST",
            )
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
            if len(payload) == GRID_BYTES:
                return payload
            preview = payload[:80].decode("ascii", errors="replace").replace("\n", " ")
            last_error = ValueError(
                f"IMD returned {len(payload):,} bytes for {day:%Y-%m-%d}; "
                f"expected {GRID_BYTES:,}. Response starts {preview!r}."
            )
        except (OSError, URLError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not retrieve IMD merged GPM rainfall for {day:%Y-%m-%d}: {last_error}")


def _complete_resume_dates(frame: pd.DataFrame) -> set[pd.Timestamp]:
    if frame.empty:
        return set()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].astype(str).str.strip().str.lower()
    complete: set[pd.Timestamp] = set()
    expected = set(RESERVOIR_CENTRES)
    for date, group in frame.groupby("date", dropna=True):
        if (
            len(group) == len(expected)
            and set(group["reservoir"]) == expected
            and group["reservoir"].nunique() == len(expected)
        ):
            complete.add(pd.Timestamp(date))
    return complete


def _write_partial(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    frame.sort_values(["date", "reservoir"], kind="stable").to_csv(
        temporary, index=False, date_format="%Y-%m-%d"
    )
    temporary.replace(path)


def build_series(
    start_date: str,
    end_date: str,
    output_path: str | Path,
    timeout: float = 60.0,
    retries: int = 2,
    delay_seconds: float = 0.2,
    checkpoint_every: int = 10,
) -> pd.DataFrame:
    start, end = pd.Timestamp(start_date).normalize(), pd.Timestamp(end_date).normalize()
    if start > end:
        raise ValueError("--start-date must be on or before --end-date.")
    if checkpoint_every < 1 or delay_seconds < 0:
        raise ValueError("Checkpoint interval must be positive and delay cannot be negative.")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing candidate {output.resolve()}; choose a new --output path."
        )
    partial = output.with_name(output.name + ".partial")
    if partial.exists():
        existing = pd.read_csv(partial)
        required = {"date", "reservoir", "rainfall_mm"}
        missing = required.difference(existing.columns)
        if missing:
            raise ValueError(f"Resume file is missing columns: {sorted(missing)}")
        complete_dates = _complete_resume_dates(existing)
        existing = existing[pd.to_datetime(existing["date"]).dt.normalize().isin(complete_dates)].copy()
        if not existing.empty:
            existing["date"] = pd.to_datetime(existing["date"]).dt.normalize()
        if any(date < start or date > end for date in complete_dates):
            raise ValueError(
                f"{partial.resolve()} contains dates outside the requested range; resume with the same range or choose another output."
            )
    else:
        existing = pd.DataFrame()
        complete_dates = set()

    dates = pd.date_range(start, end, freq="D")
    expected_bytes = len(dates) * GRID_BYTES
    print(
        f"IMD/NCMRWF merged GPM candidate: {len(dates):,} daily requests, "
        f"up to {expected_bytes / 1_000_000_000:.2f} GB of raw grid data. "
        f"Completed dates in resumable partial: {len(complete_dates):,}.",
        flush=True,
    )

    pending: list[pd.DataFrame] = []
    newly_completed = 0
    downloaded_bytes = 0
    for day in dates:
        day = pd.Timestamp(day)
        if day in complete_dates:
            continue
        payload = download_daily_payload(day, timeout=timeout, retries=retries)
        downloaded_bytes += len(payload)
        pending.append(extract_daily_payload(day, payload))
        newly_completed += 1
        if newly_completed % checkpoint_every == 0 or day == dates[-1]:
            batch = pd.concat(pending, ignore_index=True) if pending else pd.DataFrame()
            existing = pd.concat([existing, batch], ignore_index=True) if not existing.empty else batch
            _write_partial(existing, partial)
            complete_dates.update(pd.Timestamp(value) for value in batch["date"].unique())
            pending.clear()
            print(
                f"Processed {len(complete_dates):,}/{len(dates):,} days; "
                f"downloaded {downloaded_bytes / 1_000_000:.1f} MB in this run.",
                flush=True,
            )
        if delay_seconds:
            time.sleep(delay_seconds)

    # Flush a final short batch when resuming from a partial checkpoint.
    if pending:
        batch = pd.concat(pending, ignore_index=True)
        existing = pd.concat([existing, batch], ignore_index=True) if not existing.empty else batch
        _write_partial(existing, partial)
        complete_dates.update(pd.Timestamp(value) for value in batch["date"].unique())

    missing_dates = [day for day in dates if pd.Timestamp(day) not in complete_dates]
    if missing_dates:
        raise RuntimeError(
            f"Rainfall candidate is incomplete ({len(missing_dates)} missing dates). "
            f"Partial output retained at {partial.resolve()}; rerun the same command to resume."
        )

    if not partial.exists():
        # The requested range may already be complete in a prior partial file.
        _write_partial(existing, partial)
    partial.replace(output)
    result = pd.read_csv(output, parse_dates=["date"])
    print(
        f"Candidate rows: {len(result):,}; dates {result['date'].min():%Y-%m-%d} to "
        f"{result['date'].max():%Y-%m-%d}; missing site values: "
        f"{int(result['rainfall_mm'].isna().sum()):,} -> {output.resolve()}",
        flush=True,
    )
    print("This source remains separate from the standard IMD 0.25-degree archive and live forecast.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD start date.")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD end date.")
    parser.add_argument("--output", required=True, help="New candidate CSV path; existing files are not overwritten.")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-date request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retries per date after a failed/invalid response.")
    parser.add_argument("--delay-seconds", type=float, default=0.2, help="Pause between requests to avoid hammering IMD.")
    parser.add_argument("--checkpoint-every", type=int, default=10, help="Save a resumable partial CSV every N dates.")
    args = parser.parse_args()
    build_series(
        args.start_date,
        args.end_date,
        args.output,
        timeout=args.timeout,
        retries=args.retries,
        delay_seconds=args.delay_seconds,
        checkpoint_every=args.checkpoint_every,
    )


if __name__ == "__main__":
    main()
