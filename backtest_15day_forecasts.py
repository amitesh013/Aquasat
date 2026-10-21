"""Compare baseline, Prophet, and XGBoost 15-day reservoir forecasts.

Reads the merged CMWSSB ground-truth CSV and monthly CMWSSB history, excludes
all lake/date pairs listed in ground_truth_conflicts_for_review.csv, then
compares persistence, seasonal-only, seasonal-plus-damped-trend, Prophet, and
XGBoost forecasts. Every model is fit using only data available before its
forecast origin. Writes forecast_backtest_15day_results.csv in the current
directory; it does not modify the app or input datasets.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from xgboost_forecasting import build_xgb_training_pool, read_satellite_histories, xgboost_prediction


CAPACITY = {
    "poondi": 3231.0,
    "cholavaram": 1081.0,
    "puzhal": 3300.0,
    "chembarambakkam": 3645.0,
    "veeranam": 1465.0,
}
MONTHLY_LAKE_NAMES = {
    "poondi": "Poondi",
    "cholavaram": "Cholavaram",
    "puzhal": "Puzhal",
    "chembarambakkam": "Chembarambakkam",
    "veeranam": "Veeranam",
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TREND_FACTOR = 0.5
TREND_MIN_GAP_DAYS = 2
TREND_MAX_GAP_DAYS = 12


def parse_date(value: object) -> pd.Timestamp | None:
    """Parse the date formats used by the project CSVs."""
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).normalize()
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y", "%Y%m%d"):
        try:
            return pd.Timestamp(datetime.strptime(raw, fmt)).normalize()
        except ValueError:
            pass
    try:
        return pd.Timestamp(pd.to_datetime(raw, dayfirst=True, errors="raise")).normalize()
    except (ValueError, TypeError):
        return None


def read_official_series(path: Path, conflicts_path: Path) -> tuple[dict[str, pd.Series], set[tuple[str, str]]]:
    frame = pd.read_csv(path, dtype=str)
    required = {"date_str", *CAPACITY}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Ground-truth CSV is missing columns: {', '.join(sorted(missing))}")

    frame["_date"] = frame["date_str"].map(parse_date)
    frame = frame.dropna(subset=["_date"])
    conflicts_frame = pd.read_csv(conflicts_path, dtype=str)
    if not {"date", "reservoir"}.issubset(conflicts_frame.columns):
        raise ValueError("Conflict CSV needs 'date' and 'reservoir' columns.")
    excluded = set()
    for _, row in conflicts_frame.iterrows():
        date = parse_date(row.get("date"))
        lake = str(row.get("reservoir", "")).strip().lower()
        if date is not None and lake in CAPACITY:
            excluded.add((lake, date.strftime("%Y-%m-%d")))

    series_by_lake: dict[str, pd.Series] = {}
    for lake in CAPACITY:
        values = pd.to_numeric(frame[lake].str.replace(",", "", regex=False), errors="coerce")
        data = pd.DataFrame({"date": frame["_date"], "value": values}).dropna(subset=["value"])
        data = data[~data["date"].map(lambda d: (lake, d.strftime("%Y-%m-%d")) in excluded)]
        data = data.sort_values("date").drop_duplicates("date", keep="last")
        series_by_lake[lake] = pd.Series(data["value"].to_numpy(dtype=float), index=pd.DatetimeIndex(data["date"]), name=lake)
    return series_by_lake, excluded


def monthly_series(monthly: pd.DataFrame, lake: str) -> pd.Series:
    rows = monthly[monthly["Lake"].astype(str).str.strip().str.lower() == MONTHLY_LAKE_NAMES[lake].lower()]
    date_values: dict[pd.Timestamp, float] = {}
    for _, row in rows.iterrows():
        try:
            year = int(float(row["Year"]))
        except (ValueError, TypeError, KeyError):
            continue
        for month_number, month in enumerate(MONTHS, start=1):
            column = month
            if month == "Jan":
                for candidate in ("Jan (In mcft)", "Jan (in mcft)", "Jan"):
                    if candidate in rows.columns and pd.notna(row.get(candidate)):
                        column = candidate
                        break
            value = pd.to_numeric(str(row.get(column, "")).replace(",", ""), errors="coerce")
            if pd.notna(value):
                date_values[pd.Timestamp(year, month_number, 1)] = float(value)
    if not date_values:
        return pd.Series(dtype=float)
    return pd.Series(date_values, dtype=float).sort_index()


def seasonal_delta_table(history: pd.Series, before: pd.Timestamp) -> pd.Series:
    history = history[history.index < before].asfreq("MS")
    monthly_delta = history.diff().shift(-1).dropna()
    means = monthly_delta.groupby(monthly_delta.index.month).mean()
    return means.reindex(range(1, 13)).fillna(0.0)


def seasonal_forecast(origin: pd.Timestamp, last_value: float, days: int, delta_by_month: pd.Series, capacity: float) -> float:
    cumulative = 0.0
    estimate = last_value
    for step in range(1, days + 1):
        day = origin + pd.Timedelta(step, unit="D")
        cumulative += float(delta_by_month.loc[day.month]) / day.days_in_month
        estimate = float(np.clip(last_value + cumulative, 0.0, capacity))
    return estimate


def recent_slope(series: pd.Series, origin: pd.Timestamp) -> float | None:
    if origin not in series.index:
        return None
    previous = series[(series.index >= origin - pd.Timedelta(TREND_MAX_GAP_DAYS, unit="D")) &
                      (series.index <= origin - pd.Timedelta(TREND_MIN_GAP_DAYS, unit="D"))]
    if previous.empty:
        return None
    prior_date = previous.index[-1]
    gap = (origin - prior_date).days
    return float((series.loc[origin] - previous.iloc[-1]) / gap) if gap else None


def prophet_prediction(history: pd.Series, origin: pd.Timestamp, target: pd.Timestamp, capacity: float) -> float | None:
    """Fit a bounded Prophet model on official observations available at origin."""
    try:
        from prophet import Prophet
    except ImportError as exc:
        raise SystemExit("Prophet is required. Install it with: python -m pip install prophet") from exc

    known = history[history.index <= origin]
    if len(known) < 24:
        return None
    training = pd.DataFrame({"ds": known.index, "y": known.to_numpy(dtype=float)})
    training["y"] = training["y"].clip(0.0, capacity)
    training["floor"] = 0.0
    training["cap"] = capacity
    model = Prophet(
        growth="logistic",
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=0.05,
        seasonality_prior_scale=5.0,
        uncertainty_samples=0,
    )
    model.fit(training)
    future = pd.DataFrame({"ds": [target], "floor": [0.0], "cap": [capacity]})
    prediction = float(model.predict(future)["yhat"].iloc[0])
    return float(np.clip(prediction, 0.0, capacity))


def build_backtest(
    series_by_lake: dict[str, pd.Series],
    monthly_histories: dict[str, pd.Series],
    start: pd.Timestamp,
    end: pd.Timestamp,
    horizon: int,
    tolerance: int,
    satellite_histories: dict[str, pd.Series] | None = None,
    include_prophet: bool = True,
    rainfall_histories: dict[str, pd.Series] | None = None,
    rainfall_windows: tuple[int, ...] = (7,),
) -> pd.DataFrame:
    results: list[dict[str, object]] = []
    xgb_pools = {
        lake: build_xgb_training_pool(observations, horizon, tolerance, CAPACITY[lake], None)
        for lake, observations in series_by_lake.items()
    }
    satellite_xgb_pools = {
        lake: build_xgb_training_pool(
            observations, horizon, tolerance, CAPACITY[lake], satellite_histories[lake]
        )
        for lake, observations in series_by_lake.items()
        if satellite_histories and lake in satellite_histories
    }
    rainfall_xgb_pools = {
        lake: build_xgb_training_pool(
            observations,
            horizon,
            tolerance,
            CAPACITY[lake],
            None,
            rainfall_histories[lake],
            rainfall_windows,
        )
        for lake, observations in series_by_lake.items()
        if rainfall_histories and lake in rainfall_histories and not satellite_histories
    }
    satellite_rainfall_xgb_pools = {
        lake: build_xgb_training_pool(
            observations,
            horizon,
            tolerance,
            CAPACITY[lake],
            satellite_histories[lake],
            rainfall_histories[lake],
            rainfall_windows,
        )
        for lake, observations in series_by_lake.items()
        if satellite_histories and lake in satellite_histories
        and rainfall_histories and lake in rainfall_histories
    }
    xgb_config_cache: dict[str, dict[str, float | int | str]] = {}
    for lake, observations in series_by_lake.items():
        print(f"Scoring {lake}...", flush=True)
        capacity = CAPACITY[lake]
        monthly_history = monthly_histories[lake]
        for cutoff in pd.date_range(start=start, end=end, freq="MS"):
            available = observations[observations.index < cutoff]
            if len(available) < 2:
                continue
            origin = available.index[-1]
            last_value = float(available.iloc[-1])
            targets = observations[
                (observations.index >= origin + pd.Timedelta(horizon - tolerance, unit="D")) &
                (observations.index <= origin + pd.Timedelta(horizon + tolerance, unit="D"))
            ]
            if targets.empty:
                continue
            target = min(targets.index, key=lambda date: (abs((date - origin).days - horizon), date))
            actual = float(observations.loc[target])
            lead_days = (target - origin).days
            seasonal = seasonal_forecast(
                origin, last_value, lead_days, seasonal_delta_table(monthly_history, origin), capacity
            )
            slope = recent_slope(available, origin)
            hybrid = (
                float(np.clip(last_value + TREND_FACTOR * slope * lead_days, 0.0, capacity))
                if slope is not None else seasonal
            )
            predictions = {
                "persistence": last_value,
                "seasonal": seasonal,
                "seasonal_damped_trend": hybrid,
            }
            predictions["xgboost"] = xgboost_prediction(
                available, origin, target, capacity,
                training_pool=xgb_pools[lake],
                config_cache=xgb_config_cache,
                config_key=f"{lake}:storage_only",
            )
            if satellite_histories and lake in satellite_histories:
                predictions["xgboost_satellite"] = xgboost_prediction(
                    available, origin, target, capacity,
                    satellite_histories[lake], satellite_xgb_pools[lake],
                    xgb_config_cache, f"{lake}:satellite",
                )
            if rainfall_histories and lake in rainfall_histories:
                rain = rainfall_histories[lake]
                for days in rainfall_windows:
                    window = rain[
                        (rain.index >= origin - pd.DateOffset(days=int(days))) &
                        (rain.index < origin)
                    ]
                    if len(window) != days or window.index.nunique() != days or window.isna().any():
                        raise ValueError(
                            f"Incomplete {days}-day rainfall window for {lake} before "
                            f"{origin:%Y-%m-%d}; refusing to score a missing rainfall feature."
                        )
                if satellite_histories and lake in satellite_histories:
                    predictions["xgboost_satellite_rainfall"] = xgboost_prediction(
                        available,
                        origin,
                        target,
                        capacity,
                        satellite_history=satellite_histories[lake],
                        training_pool=satellite_rainfall_xgb_pools[lake],
                        config_cache=xgb_config_cache,
                        config_key=(
                            f"{lake}:satellite_rainfall:"
                            f"{','.join(map(str, rainfall_windows))}"
                        ),
                        rainfall_history=rain,
                        rainfall_windows=rainfall_windows,
                    )
                else:
                    predictions["xgboost_rainfall"] = xgboost_prediction(
                        available,
                        origin,
                        target,
                        capacity,
                        satellite_history=None,
                        training_pool=rainfall_xgb_pools[lake],
                        config_cache=xgb_config_cache,
                        config_key=f"{lake}:storage_only_rainfall:{','.join(map(str, rainfall_windows))}",
                        rainfall_history=rain,
                        rainfall_windows=rainfall_windows,
                    )
            if include_prophet:
                predictions["prophet"] = prophet_prediction(available, origin, target, capacity)
            row: dict[str, object] = {
                "reservoir": lake,
                "cutoff_date": cutoff.strftime("%Y-%m-%d"),
                "origin_date": origin.strftime("%Y-%m-%d"),
                "target_date": target.strftime("%Y-%m-%d"),
                "lead_days": lead_days,
                "actual_mcft": actual,
                "capacity_mcft": capacity,
            }
            for method, prediction in predictions.items():
                if prediction is None:
                    continue
                row[f"{method}_prediction_mcft"] = prediction
                row[f"{method}_error_mcft"] = prediction - actual
            results.append(row)
    return pd.DataFrame(results)


def print_summary(results: pd.DataFrame) -> None:
    print("\n15-day rolling-origin forecast backtest (lower RMSE/MAE is better)")
    if results.empty:
        print("No eligible forecast/actual pairs found. Check the input date range and official readings.")
        return
    summary = []
    methods = (
        "persistence", "seasonal", "seasonal_damped_trend", "xgboost",
        "xgboost_satellite", "xgboost_rainfall",
        "xgboost_satellite_rainfall", "prophet",
    )
    for lake in CAPACITY:
        lake_rows = results[results["reservoir"] == lake]
        for method in methods:
            column = f"{method}_error_mcft"
            if column not in lake_rows.columns:
                continue
            errors = lake_rows[column].dropna().to_numpy(dtype=float)
            if not len(errors):
                continue
            cap = CAPACITY[lake]
            summary.append({
                "Lake": lake,
                "Method": method,
                "Pairs": len(errors),
                "RMSE mcft": np.sqrt(np.mean(errors ** 2)),
                "RMSE % capacity": np.sqrt(np.mean(errors ** 2)) / cap * 100,
                "MAE mcft": np.mean(np.abs(errors)),
                "Bias mcft": np.mean(errors),
            })
    print(pd.DataFrame(summary).to_string(index=False, formatters={
        "RMSE mcft": "{:.1f}".format,
        "RMSE % capacity": "{:.1f}%".format,
        "MAE mcft": "{:.1f}".format,
        "Bias mcft": "{:+.1f}".format,
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", default="reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv")
    parser.add_argument("--monthly", default="chennai_lakes_combined.csv")
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2026-08-01")
    parser.add_argument("--horizon-days", type=int, default=15)
    parser.add_argument("--target-window-days", type=int, default=3,
                        help="Accept actual readings within this many days of the horizon.")
    parser.add_argument("--satellite", default="sentinel2_v3_clear95_accuracy_ready.csv",
                        help="Quality-filtered Sentinel-2 matches or the OOS storage candidate from build_sentinel2_storage.py.")
    parser.add_argument("--no-satellite", action="store_true",
                        help="Disable all satellite features for a storage-only candidate comparison.")
    parser.add_argument("--rainfall", help="Optional daily rainfall CSV; adds rainfall-assisted XGBoost candidates. When satellite history is available, also scores the combined satellite-plus-rainfall model.")
    parser.add_argument("--rainfall-windows", nargs="+", type=int, choices=(7, 30, 90), default=[7],
                        help="Antecedent rainfall accumulation windows. Defaults to 7 days when --rainfall is supplied.")
    parser.add_argument("--skip-prophet", action="store_true",
                        help="Skip Prophet so the XGBoost experiment finishes sooner.")
    parser.add_argument("--output", default="forecast_backtest_15day_results.csv")
    args = parser.parse_args()

    ground_truth_path = Path(args.ground_truth)
    monthly_path = Path(args.monthly)
    conflicts_path = Path(args.conflicts)
    for path in (ground_truth_path, monthly_path, conflicts_path):
        if not path.exists():
            raise SystemExit(f"Required input file not found: {path.resolve()}")

    series_by_lake, excluded = read_official_series(ground_truth_path, conflicts_path)
    monthly_frame = pd.read_csv(monthly_path)
    monthly_histories = {lake: monthly_series(monthly_frame, lake) for lake in CAPACITY}
    satellite_path = Path(args.satellite)
    satellite_histories = (
        {} if args.no_satellite else
        read_satellite_histories(satellite_path, CAPACITY, excluded_pairs=excluded)
        if satellite_path.exists() else {}
    )
    if satellite_histories:
        print(f"Satellite-derived lagged features loaded for {len(satellite_histories)} reservoirs from {satellite_path}")
        print("Satellite estimates are out-of-sample: each image uses only earlier, non-disputed exact-date matches.")
    else:
        print("Satellite features are disabled; XGBoost candidates use storage history only.")
    rainfall_histories = None
    rainfall_windows: tuple[int, ...] = tuple()
    if args.rainfall:
        rainfall_path = Path(args.rainfall)
        if not rainfall_path.exists():
            raise SystemExit(f"Rainfall input file not found: {rainfall_path.resolve()}")
        rainfall_frame = pd.read_csv(rainfall_path)
        required_rainfall = {"date", "reservoir", "rainfall_mm"}
        missing_rainfall = required_rainfall.difference(rainfall_frame.columns)
        if missing_rainfall:
            raise SystemExit(
                "Rainfall input is missing columns: " + ", ".join(sorted(missing_rainfall))
            )
        rainfall_frame["date"] = pd.to_datetime(rainfall_frame["date"], errors="coerce").dt.normalize()
        rainfall_frame["reservoir"] = rainfall_frame["reservoir"].astype(str).str.strip().str.lower()
        rainfall_frame["rainfall_mm"] = pd.to_numeric(rainfall_frame["rainfall_mm"], errors="coerce")
        rainfall_frame = rainfall_frame.dropna(subset=["date"])
        rainfall_histories = {}
        for lake in CAPACITY:
            group = rainfall_frame[rainfall_frame["reservoir"] == lake].sort_values("date")
            group = group.drop_duplicates("date", keep="last")
            if group.empty:
                raise SystemExit(f"No rainfall history found for {lake} in {rainfall_path.resolve()}")
            rainfall_histories[lake] = pd.Series(
                group["rainfall_mm"].to_numpy(dtype=float),
                index=pd.DatetimeIndex(group["date"]),
                name=lake,
            ).sort_index()
        rainfall_windows = tuple(args.rainfall_windows)
        if not rainfall_windows or len(set(rainfall_windows)) != len(rainfall_windows):
            raise SystemExit("--rainfall-windows must contain unique values from 7, 30, and 90.")
        satellite_note = (
            "the combined model also includes satellite-history features."
            if satellite_histories else
            "satellite features are disabled for this comparison."
        )
        print(
            f"Rainfall candidate enabled for {len(rainfall_histories)} reservoirs; "
            f"windows: {', '.join(f'{days}d' for days in rainfall_windows)}; "
            f"{satellite_note}"
        )
    results = build_backtest(
        series_by_lake,
        monthly_histories,
        pd.Timestamp(args.start_date),
        pd.Timestamp(args.end_date),
        args.horizon_days,
        args.target_window_days,
        satellite_histories,
        include_prophet=not args.skip_prophet,
        rainfall_histories=rainfall_histories,
        rainfall_windows=rainfall_windows or (7,),
    )
    output_path = Path(args.output)
    results.to_csv(output_path, index=False)
    print(f"Excluded disputed lake/date pairs: {len(excluded)}")
    print(f"Forecast/actual pairs written: {len(results)}")
    print(f"Detailed results: {output_path.resolve()}")
    print_summary(results)


if __name__ == "__main__":
    main()
