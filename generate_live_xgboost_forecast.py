"""Generate the dashboard's 15-day XGBoost forecast with satellite and rainfall features.

The model predicts future official storage from earlier official observations
and, when available, the latest earlier out-of-sample satellite storage
estimate. The combined satellite file prefers Sentinel-2 and uses a Sentinel-1
candidate only on a lake/date where Sentinel-2 is unavailable. Antecedent GPM
rainfall totals use complete daily windows strictly before each forecast origin.
The daily chart path interpolates to the independently modelled day-15 endpoint.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_15day_forecasts import CAPACITY, read_official_series
from xgboost_forecasting import (
    build_xgb_training_pool,
    rainfall_window_totals,
    read_rainfall_histories,
    read_satellite_histories,
    xgboost_prediction,
)


DEFAULT_GROUND_TRUTH = "reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv"
DEFAULT_CONFLICTS = "ground_truth_conflicts_for_review.csv"
DEFAULT_SATELLITE = "sentinel2_sentinel1_oos_storage_candidate.csv"
DEFAULT_RAINFALL = "rainfall_gpm_2015_2026_to_2026-10-20_candidate.csv"
DEFAULT_BACKTEST = "forecast_backtest_s2_s1_candidate.csv"
DEFAULT_OUTPUT = "reservoir_forecasts.csv"
RAINFALL_WINDOWS = (7, 30, 90)
RAINFALL_SOURCE = "IMD/NCMRWF merged gauge + GPM daily rainfall"
HORIZON_DAYS = 15
TARGET_TOLERANCE_DAYS = 3


def load_backtest_errors(
    path: Path,
    error_column: str = "xgboost_satellite_rainfall_error_mcft",
) -> dict[str, dict[str, float | int | str]]:
    frame = pd.read_csv(path)
    if error_column not in frame and error_column == "xgboost_satellite_rainfall_error_mcft":
        # Preserve a usable empirical display range until this rainfall-added
        # model has its own long rolling-origin error history. Label it clearly.
        error_column = "xgboost_satellite_error_mcft"
        interval_source = "S2/S1-only reference backtest; rainfall model range not yet independently calibrated"
    else:
        interval_source = "rainfall-assisted S2/S1 rolling-origin backtest"
    required = {"reservoir", "lead_days", error_column}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            f"Backtest file is missing required columns: {', '.join(sorted(missing))}"
        )
    frame["reservoir"] = frame["reservoir"].astype(str).str.strip().str.lower()
    frame["lead_days"] = pd.to_numeric(frame["lead_days"], errors="coerce")
    frame[error_column] = pd.to_numeric(frame[error_column], errors="coerce")
    frame = frame[
        frame["lead_days"].between(
            HORIZON_DAYS - TARGET_TOLERANCE_DAYS,
            HORIZON_DAYS + TARGET_TOLERANCE_DAYS,
        )
    ].dropna(subset=[error_column])

    summaries: dict[str, dict[str, float | int]] = {}
    for lake in CAPACITY:
        errors = frame.loc[
            frame["reservoir"] == lake, error_column
        ].to_numpy(dtype=float)
        if len(errors) < 20:
            raise ValueError(
                f"Only {len(errors)} satellite-assisted XGBoost backtest rows for {lake}; "
                "at least 20 are required to make the displayed empirical range."
            )
        summaries[lake] = {
            "error_q10": float(np.quantile(errors, 0.10)),
            "error_q90": float(np.quantile(errors, 0.90)),
            "rmse": float(np.sqrt(np.mean(np.square(errors)))),
            "pairs": int(len(errors)),
            "interval_source": interval_source,
        }
    return summaries


def canonical_lake(value: object) -> str:
    name = str(value).strip().lower()
    if name in {"puzhal (redhills)", "redhills", "red hills"}:
        return "puzhal"
    return name


def latest_satellite_metadata(
    path: Path,
    lake: str,
    origin: pd.Timestamp,
    excluded_pairs: set[tuple[str, str]],
) -> dict[str, object]:
    frame = pd.read_csv(path)
    required = {"date", "reservoir", "source"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            f"Satellite file is missing required columns: {', '.join(sorted(missing))}"
        )
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].map(canonical_lake)
    if "quality_pass" in frame:
        frame = frame[
            frame["quality_pass"].astype(str).str.strip().str.lower().isin(
                {"true", "1", "yes"}
            )
        ]
    frame = frame.dropna(subset=["date"])
    frame = frame[
        (frame["reservoir"] == lake) & (frame["date"] <= origin)
    ].copy()
    if not frame.empty:
        frame = frame[
            ~frame.apply(
                lambda row: (
                    row["reservoir"], row["date"].strftime("%Y-%m-%d")
                ) in excluded_pairs,
                axis=1,
            )
        ]
    if frame.empty:
        return {
            "satellite_source": "unavailable",
            "satellite_feature_date": "",
            "satellite_age_days": "",
        }

    row = frame.sort_values("date", kind="stable").iloc[-1]
    source = str(row["source"]).strip().lower()
    label = "Sentinel-1 fallback" if "sentinel1" in source else "Sentinel-2"
    feature_date = pd.Timestamp(row["date"]).normalize()
    return {
        "satellite_source": label,
        "satellite_feature_date": feature_date.strftime("%Y-%m-%d"),
        "satellite_age_days": int((origin - feature_date).days),
    }


def generate_forecast(
    ground_truth_path: str | Path = DEFAULT_GROUND_TRUTH,
    conflicts_path: str | Path = DEFAULT_CONFLICTS,
    satellite_path: str | Path = DEFAULT_SATELLITE,
    backtest_path: str | Path = DEFAULT_BACKTEST,
    output_path: str | Path = DEFAULT_OUTPUT,
    horizon_days: int = HORIZON_DAYS,
    rainfall_path: str | Path = DEFAULT_RAINFALL,
    rainfall_windows: tuple[int, ...] = RAINFALL_WINDOWS,
) -> pd.DataFrame:
    if horizon_days != HORIZON_DAYS:
        raise ValueError(
            "This live model is validated for a 15-day horizon; keep --horizon-days at 15."
        )

    ground_truth_path = Path(ground_truth_path)
    conflicts_path = Path(conflicts_path)
    satellite_path = Path(satellite_path)
    backtest_path = Path(backtest_path)
    rainfall_path = Path(rainfall_path)
    for path in (ground_truth_path, conflicts_path, satellite_path, backtest_path, rainfall_path):
        if not path.exists():
            raise FileNotFoundError(f"Required forecast input not found: {path.resolve()}")
    if (
        not rainfall_windows
        or len(set(rainfall_windows)) != len(rainfall_windows)
        or any(days not in (7, 30, 90) for days in rainfall_windows)
    ):
        raise ValueError("Rainfall windows must be unique values selected from 7, 30, and 90.")

    official_histories, excluded_pairs = read_official_series(
        ground_truth_path, conflicts_path
    )
    satellite_histories = read_satellite_histories(
        satellite_path, CAPACITY, excluded_pairs=excluded_pairs
    )
    if len(satellite_histories) != len(CAPACITY):
        missing_lakes = sorted(set(CAPACITY).difference(satellite_histories))
        raise ValueError(
            "The S2-first/S1-fallback file has no quality-passing history for: "
            + ", ".join(missing_lakes)
        )
    rainfall_histories = read_rainfall_histories(rainfall_path, CAPACITY)
    errors = load_backtest_errors(
        backtest_path, "xgboost_satellite_rainfall_error_mcft"
    )

    rows: list[dict[str, object]] = []
    report: list[dict[str, object]] = []
    config_cache: dict[str, dict[str, float | int | str]] = {}
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for lake, capacity in CAPACITY.items():
        history = official_histories[lake].dropna().sort_index()
        if len(history) < 35:
            raise ValueError(
                f"Only {len(history)} non-disputed official readings for {lake}; "
                "not enough history for the live XGBoost forecast."
            )
        origin = pd.Timestamp(history.index[-1]).normalize()
        latest_value = float(history.iloc[-1])
        target = origin + pd.Timedelta(HORIZON_DAYS, unit="D")
        satellite_history = satellite_histories[lake]
        rainfall_history = rainfall_histories[lake]
        rainfall_totals = rainfall_window_totals(
            rainfall_history, origin, rainfall_windows
        )
        incomplete_windows = [
            days for days, total in rainfall_totals.items() if not np.isfinite(total)
        ]
        if incomplete_windows:
            raise ValueError(
                f"Incomplete antecedent rainfall windows for {lake} before "
                f"{origin:%Y-%m-%d}: {incomplete_windows}. Refusing to silently "
                "forecast without the requested rainfall features."
            )
        training_pool = build_xgb_training_pool(
            history,
            HORIZON_DAYS,
            TARGET_TOLERANCE_DAYS,
            capacity,
            satellite_history,
            rainfall_history,
            rainfall_windows,
        )
        endpoint = xgboost_prediction(
            history,
            origin,
            target,
            capacity,
            satellite_history=satellite_history,
            training_pool=training_pool,
            config_cache=config_cache,
            config_key=(
                f"live:{lake}:s2_s1_gpm_rain_{','.join(map(str, rainfall_windows))}:"
                f"{HORIZON_DAYS}d"
            ),
            rainfall_history=rainfall_history,
            rainfall_windows=rainfall_windows,
        )
        if endpoint is None or not np.isfinite(endpoint):
            raise RuntimeError(
                f"XGBoost could not fit a 15-day model for {lake}; "
                "the existing forecast file was left unchanged."
            )
        endpoint = float(np.clip(endpoint, 0.0, capacity))

        source_info = latest_satellite_metadata(
            satellite_path, lake, origin, excluded_pairs
        )
        error_summary = errors[lake]
        q10_error = float(error_summary["error_q10"])
        q90_error = float(error_summary["error_q90"])
        for lead in range(1, HORIZON_DAYS + 1):
            progress = lead / HORIZON_DAYS
            estimate = float(
                np.clip(latest_value + (endpoint - latest_value) * progress, 0.0, capacity)
            )
            # Backtest residuals are scaled toward the known origin to make a
            # readable fan. This is an empirical range, not a probability interval.
            uncertainty_scale = float(np.sqrt(progress))
            lower = float(np.clip(estimate - q90_error * uncertainty_scale, 0.0, capacity))
            upper = float(np.clip(estimate - q10_error * uncertainty_scale, 0.0, capacity))
            rows.append({
                "ds": (origin + pd.Timedelta(lead, unit="D")).strftime("%Y-%m-%d"),
                "yhat": estimate,
                "yhat_lower": min(lower, estimate),
                "yhat_upper": max(upper, estimate),
                "reservoir": lake,
                "model": "xgboost_satellite_s2_first_s1_fallback_gpm_rainfall",
                "generated_at_utc": generated_at,
                "forecast_origin": origin.strftime("%Y-%m-%d"),
                "horizon_days": HORIZON_DAYS,
                "day15_prediction_mcft": endpoint,
                "satellite_source": source_info["satellite_source"],
                "satellite_feature_date": source_info["satellite_feature_date"],
                "satellite_age_days": source_info["satellite_age_days"],
                "rainfall_source": RAINFALL_SOURCE,
                "rainfall_window_days": ",".join(map(str, rainfall_windows)),
                "rainfall_feature_through_date": (origin - pd.DateOffset(days=1)).strftime("%Y-%m-%d"),
                **{f"rainfall_{days}d_mm": rainfall_totals[days] for days in rainfall_windows},
                "backtest_rmse_mcft": error_summary["rmse"],
                "backtest_pairs": error_summary["pairs"],
                "backtest_error_model": error_summary["interval_source"],
                "interval_method": (
                    f"Approximate rolling-origin 10th-90th error range; "
                    f"reference: {error_summary['interval_source']}."
                ),
            })

        report.append({
            "reservoir": lake,
            "origin": origin.strftime("%Y-%m-%d"),
            "latest_official_mcft": latest_value,
            "satellite_source": source_info["satellite_source"],
            "satellite_date": source_info["satellite_feature_date"],
            "satellite_age_days": source_info["satellite_age_days"],
            **{f"rainfall_{days}d_mm": rainfall_totals[days] for days in rainfall_windows},
            "day15_forecast_mcft": endpoint,
            "reference_backtest_rmse_mcft": error_summary["rmse"],
            "reference_error_source": error_summary["interval_source"],
            "backtest_pairs": error_summary["pairs"],
        })

    output = pd.DataFrame(rows)
    expected_rows = len(CAPACITY) * HORIZON_DAYS
    if len(output) != expected_rows or output[["yhat", "yhat_lower", "yhat_upper"]].isna().any().any():
        raise RuntimeError("Forecast validation failed; the existing output was left unchanged.")

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    output.to_csv(temporary, index=False)
    temporary.replace(destination)

    print("Live 15-day XGBoost forecast (official storage + S2/S1 history + antecedent GPM rainfall)")
    print(pd.DataFrame(report).to_string(index=False, formatters={
        "latest_official_mcft": "{:.1f}".format,
        "day15_forecast_mcft": "{:.1f}".format,
        "reference_backtest_rmse_mcft": "{:.1f}".format,
    }))
    print(f"Forecast rows written: {len(output)} -> {destination.resolve()}")
    print(
        "The day-15 endpoint is model-generated; the daily path is interpolated. "
        "The shaded range uses the documented reference backtest errors and is "
        "not calibrated specifically for the rainfall-assisted model."
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--conflicts", default=DEFAULT_CONFLICTS)
    parser.add_argument("--satellite", default=DEFAULT_SATELLITE)
    parser.add_argument("--rainfall", default=DEFAULT_RAINFALL)
    parser.add_argument(
        "--rainfall-windows",
        nargs="+",
        type=int,
        choices=(7, 30, 90),
        default=list(RAINFALL_WINDOWS),
    )
    parser.add_argument("--backtest", default=DEFAULT_BACKTEST)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--horizon-days", type=int, default=HORIZON_DAYS)
    args = parser.parse_args()
    generate_forecast(
        args.ground_truth,
        args.conflicts,
        args.satellite,
        args.backtest,
        args.output,
        args.horizon_days,
        args.rainfall,
        tuple(args.rainfall_windows),
    )


if __name__ == "__main__":
    main()
