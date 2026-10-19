"""Build a small reservoir-local rainfall series from IMD 0.25-degree grids.

The input is official IMD daily gridded rainfall (millimetres). Each reservoir
is represented by its nearest grid cell to a public map centre coordinate. The
cell is a local rainfall proxy, not a reservoir catchment-average measurement.
"""

from __future__ import annotations

import argparse
import math
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
from scipy.io import netcdf_file


IMD_DOWNLOAD_URL = "https://imdpune.gov.in/cmpg/Griddata/RF25.php"
SOURCE_NAME = "IMD daily gridded rainfall, 0.25 degree, mm"
SOURCE_PAGE = "https://imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html"

# Representative lake-centre coordinates; rainfall uses the closest IMD grid
# centre. These are not the v3 boundary centroids or catchment polygons.
RESERVOIR_CENTRES = {
    "poondi": (13.1823, 79.8567),
    "cholavaram": (13.22804, 80.15193),
    "puzhal": (13.16496, 80.16315),
    "chembarambakkam": (13.00793, 80.05078),
    "veeranam": (11.34454, 79.52858),
}


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace")
    return str(value)


def download_year(year: int, destination: Path) -> None:
    """Download one annual NetCDF file from the published IMD form."""
    body = urlencode({"RF25": str(year)}).encode("ascii")
    request = Request(
        IMD_DOWNLOAD_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "AquaSat research demo rainfall downloader",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            payload = response.read()
    except (OSError, URLError) as exc:
        raise RuntimeError(f"Could not download IMD rainfall for {year}: {exc}") from exc
    if not payload.startswith(b"CDF"):
        raise RuntimeError(
            f"IMD did not return a NetCDF file for {year}; the download was not saved."
        )
    destination.write_bytes(payload)


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


def extract_year(path: Path, expected_year: int) -> pd.DataFrame:
    """Read rainfall at each reservoir's nearest IMD grid point."""
    with netcdf_file(path, "r", mmap=False) as dataset:
        required = {"TIME", "LATITUDE", "LONGITUDE", "RAINFALL"}
        missing = required.difference(dataset.variables)
        if missing:
            raise ValueError(f"{path} lacks NetCDF variables: {sorted(missing)}")
        time_values = np.asarray(dataset.variables["TIME"].data, dtype=float)
        latitudes = np.asarray(dataset.variables["LATITUDE"].data, dtype=float)
        longitudes = np.asarray(dataset.variables["LONGITUDE"].data, dtype=float)
        rain_variable = dataset.variables["RAINFALL"]
        rainfall = np.asarray(rain_variable.data, dtype=float)
        time_units = _decode(rain_variable._attributes.get("units", "mm"))
        time_origin = _decode(dataset.variables["TIME"]._attributes.get("units", ""))
        missing_value = float(
            rain_variable._attributes.get(
                "missing_value", rain_variable._attributes.get("_FillValue", -999.0)
            )
        )

        if "since" not in time_origin:
            raise ValueError(f"Unrecognized IMD time coordinate in {path}: {time_origin}")
        origin_text = time_origin.split("since", 1)[1].strip().split(" ")[0]
        origin = pd.Timestamp(origin_text)
        dates = origin + pd.to_timedelta(time_values, unit="D")
        if len(dates) and not (dates.year == expected_year).all():
            raise ValueError(f"Expected {expected_year} dates in {path}; got {dates.min()}..{dates.max()}")

        records: list[dict[str, object]] = []
        for reservoir, (site_lat, site_lon) in RESERVOIR_CENTRES.items():
            lat_index = int(np.abs(latitudes - site_lat).argmin())
            lon_index = int(np.abs(longitudes - site_lon).argmin())
            grid_lat = float(latitudes[lat_index])
            grid_lon = float(longitudes[lon_index])
            values = rainfall[:, lat_index, lon_index].astype(float)
            values[np.isclose(values, missing_value)] = np.nan
            values[values < 0] = np.nan
            for date, amount in zip(dates, values):
                records.append({
                    "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
                    "reservoir": reservoir,
                    "rainfall_mm": float(amount) if np.isfinite(amount) else np.nan,
                    "site_latitude": site_lat,
                    "site_longitude": site_lon,
                    "grid_latitude": grid_lat,
                    "grid_longitude": grid_lon,
                    "grid_offset_km": _great_circle_km(site_lat, site_lon, grid_lat, grid_lon),
                    "rainfall_units": time_units,
                    "source_dataset": SOURCE_NAME,
                    "source_url": SOURCE_PAGE,
                    "spatial_support": "nearest 0.25-degree grid cell to reservoir centre; not catchment mean",
                })

    return pd.DataFrame(records)


def build_series(
    start_year: int = 2016,
    end_year: int = 2019,
    source_dir: str | Path = ".",
    output_path: str | Path = "rainfall_reservoir_daily_2016_2019.csv",
) -> pd.DataFrame:
    if start_year < 1901 or end_year > 2024 or start_year > end_year:
        raise ValueError("Choose an inclusive year range from 1901 through 2024, the latest year listed in the IMD archive.")
    source_dir = Path(source_dir)
    frames: list[pd.DataFrame] = []
    # Keep scratch downloads inside the project workspace. On Windows the
    # system temp folder may be sandbox-protected, and TemporaryDirectory's
    # cleanup then fails even after a successful extraction.
    with tempfile.TemporaryDirectory(prefix=".aquasat_imd_rain_", dir=source_dir) as temp_name:
        temp_dir = Path(temp_name)
        for year in range(start_year, end_year + 1):
            cached = source_dir / f"imd_rainfall_0p25_{year}.nc"
            netcdf_path = cached if cached.exists() else temp_dir / cached.name
            if not netcdf_path.exists():
                download_year(year, netcdf_path)
            frames.append(extract_year(netcdf_path, year))

    output = pd.concat(frames, ignore_index=True)
    output["date"] = pd.to_datetime(output["date"], errors="raise")
    output = output.sort_values(["reservoir", "date"], kind="stable")
    output = output.drop_duplicates(["reservoir", "date"], keep="last")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    output.to_csv(temporary, index=False, date_format="%Y-%m-%d")
    temporary.replace(destination)
    print(f"IMD rainfall rows: {len(output)} -> {destination.resolve()}")
    print(
        f"Dates: {output['date'].min():%Y-%m-%d} to {output['date'].max():%Y-%m-%d}; "
        f"missing rain values: {int(output['rainfall_mm'].isna().sum())}."
    )
    print(
        "Rainfall is a nearest-cell local proxy at 0.25-degree resolution, "
        "not basin/catchment-averaged precipitation."
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2016)
    parser.add_argument("--end-year", type=int, default=2019)
    parser.add_argument("--source-dir", default=".")
    parser.add_argument("--output", default="rainfall_reservoir_daily_2016_2019.csv")
    args = parser.parse_args()
    build_series(args.start_year, args.end_year, args.source_dir, args.output)


if __name__ == "__main__":
    main()
