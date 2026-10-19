"""Compare the current XGBoost feature set with antecedent IMD rainfall.

This is a historical research backtest. It never promotes a model or overwrites
the live forecast. Only rainfall dated strictly before each forecast origin is
available to either model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_15day_forecasts import CAPACITY, parse_date, read_official_series
from xgboost_forecasting import (
    build_xgb_training_pool,
    read_satellite_histories,
    xgboost_prediction,
)


DEFAULT_OFFICIAL = "reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv"
DEFAULT_CONFLICTS = "ground_truth_conflicts_for_review.csv"
DEFAULT_SATELLITE = "sentinel2_sentinel1_oos_storage_candidate.csv"
DEFAULT_BASE_BACKTEST = "forecast_backtest_s2_s1_candidate.csv"
DEFAULT_RAINFALL = "rainfall_reservoir_daily_2016_2019.csv"
DEFAULT_PREDICTIONS = "rainfall_2019_crisis_predictions.csv"
DEFAULT_SUMMARY = "rainfall_2019_crisis_summary.csv"


def read_rainfall(path: Path) -> dict[str, pd.Series]:
    frame = pd.read_csv(path, parse_dates=["date"])
    required = {"date", "reservoir", "rainfall_mm"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Rainfall CSV is missing columns: {', '.join(sorted(missing))}")
    frame["reservoir"] = frame["reservoir"].astype(str).str.strip().str.lower()
    frame["rainfall_mm"] = pd.to_numeric(frame["rainfall_mm"], errors="coerce")
    histories: dict[str, pd.Series] = {}
    for lake in CAPACITY:
        group = frame[frame["reservoir"] == lake].dropna(subset=["date", "rainfall_mm"])
        group = group.sort_values("date").drop_duplicates("date", keep="last")
        if group.empty:
            raise ValueError(f"No rainfall history found for {lake}.")
        histories[lake] = pd.Series(
            group["rainfall_mm"].to_numpy(dtype=float),
            index=pd.DatetimeIndex(group["date"]).normalize(),
            name=lake,
        ).sort_index()
    return histories


def _metrics(group: pd.DataFrame, label: str, prediction: str) -> dict[str, object]:
    errors = group[prediction] - group["actual_mcft"]
    baseline_percent_errors = group["baseline_error_mcft"] / group["capacity_mcft"] * 100.0
    rainfall_percent_errors = errors / group["capacity_mcft"] * 100.0
    return {
        "reservoir": label,
        "pairs": len(group),
        "baseline_rmse_mcft": float(np.sqrt(np.mean(np.square(group["baseline_error_mcft"])))),
        "rainfall_rmse_mcft": float(np.sqrt(np.mean(np.square(errors)))),
        "rmse_change_mcft": float(
            np.sqrt(np.mean(np.square(errors)))
            - np.sqrt(np.mean(np.square(group["baseline_error_mcft"])))
        ),
        "baseline_rmse_pct_capacity": float(np.sqrt(np.mean(np.square(baseline_percent_errors)))),
        "rainfall_rmse_pct_capacity": float(np.sqrt(np.mean(np.square(rainfall_percent_errors)))),
        "rmse_change_pct_capacity": float(
            np.sqrt(np.mean(np.square(rainfall_percent_errors)))
            - np.sqrt(np.mean(np.square(baseline_percent_errors)))
        ),
        "baseline_mae_mcft": float(np.mean(np.abs(group["baseline_error_mcft"]))),
        "rainfall_mae_mcft": float(np.mean(np.abs(errors))),
        "rainfall_bias_mcft": float(errors.mean()),
        "below_300_pairs": int((group["actual_mcft"] < 300).sum()),
        "baseline_below_300_rmse_mcft": (
            float(np.sqrt(np.mean(np.square(group.loc[group["actual_mcft"] < 300, "baseline_error_mcft"]))))
            if (group["actual_mcft"] < 300).any() else np.nan
        ),
        "rainfall_below_300_rmse_mcft": (
            float(np.sqrt(np.mean(np.square(errors[group["actual_mcft"] < 300]))))
            if (group["actual_mcft"] < 300).any() else np.nan
        ),
    }


def run_backtest(
    official_path: str | Path = DEFAULT_OFFICIAL,
    conflicts_path: str | Path = DEFAULT_CONFLICTS,
    satellite_path: str | Path = DEFAULT_SATELLITE,
    base_backtest_path: str | Path = DEFAULT_BASE_BACKTEST,
    rainfall_path: str | Path = DEFAULT_RAINFALL,
    start_date: str = "2019-01-01",
    end_date: str = "2019-08-31",
    predictions_path: str | Path = DEFAULT_PREDICTIONS,
    summary_path: str | Path = DEFAULT_SUMMARY,
    yearly_summary_path: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    official_path, conflicts_path = Path(official_path), Path(conflicts_path)
    satellite_path, base_backtest_path = Path(satellite_path), Path(base_backtest_path)
    rainfall_path = Path(rainfall_path)
    for path in (official_path, conflicts_path, satellite_path, base_backtest_path, rainfall_path):
        if not path.exists():
            raise FileNotFoundError(f"Required input missing: {path.resolve()}")

    official, excluded = read_official_series(official_path, conflicts_path)
    satellites = read_satellite_histories(satellite_path, CAPACITY, excluded_pairs=excluded)
    rain_by_lake = read_rainfall(rainfall_path)
    target_rows = pd.read_csv(base_backtest_path, dtype=str)
    needed = {"reservoir", "origin_date", "target_date", "lead_days"}
    missing = needed.difference(target_rows.columns)
    if missing:
        raise ValueError(f"Base backtest is missing columns: {', '.join(sorted(missing))}")
    target_rows["reservoir"] = target_rows["reservoir"].astype(str).str.strip().str.lower()
    target_rows["origin_date"] = target_rows["origin_date"].map(parse_date)
    target_rows["target_date"] = target_rows["target_date"].map(parse_date)
    target_rows["lead_days"] = pd.to_numeric(target_rows["lead_days"], errors="coerce")
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if start > end:
        raise ValueError("--start-date must be on or before --end-date.")
    target_rows = target_rows[
        target_rows["target_date"].between(start, end)
        & target_rows["origin_date"].notna()
        & target_rows["lead_days"].between(12, 18)
    ].copy()

    result_rows: list[dict[str, object]] = []
    baseline_configs: dict[str, dict[str, float | int | str]] = {}
    rain_configs: dict[str, dict[str, float | int | str]] = {}
    for lake, capacity in CAPACITY.items():
        history = official[lake].dropna().sort_index()
        sat_history = satellites.get(lake)
        rainfall = rain_by_lake[lake]
        baseline_pool = build_xgb_training_pool(
            history, 15, 3, capacity, sat_history, rainfall_history=None
        )
        rain_pool = build_xgb_training_pool(
            history, 15, 3, capacity, sat_history, rainfall_history=rainfall
        )
        lake_targets = target_rows[target_rows["reservoir"] == lake].sort_values("target_date")
        for row in lake_targets.itertuples(index=False):
            origin, target = pd.Timestamp(row.origin_date), pd.Timestamp(row.target_date)
            actual = history.get(target, np.nan)
            if pd.isna(actual) or target not in history.index:
                continue
            baseline = xgboost_prediction(
                history, origin, target, capacity,
                satellite_history=sat_history,
                training_pool=baseline_pool,
                config_cache=baseline_configs,
                config_key=f"2019:baseline:{lake}",
            )
            with_rain = xgboost_prediction(
                history, origin, target, capacity,
                satellite_history=sat_history,
                training_pool=rain_pool,
                config_cache=rain_configs,
                config_key=f"2019:rainfall:{lake}",
                rainfall_history=rainfall,
            )
            if baseline is None or with_rain is None:
                continue
            antecedent_30d = rainfall[
                (rainfall.index >= origin - pd.Timedelta(30, unit="D"))
                & (rainfall.index < origin)
            ]
            result_rows.append({
                "reservoir": lake,
                "origin_date": origin.strftime("%Y-%m-%d"),
                "target_date": target.strftime("%Y-%m-%d"),
                "lead_days": int((target - origin).days),
                "actual_mcft": float(actual),
                "capacity_mcft": float(capacity),
                "baseline_s2_s1_xgboost_mcft": float(baseline),
                "rainfall_s2_s1_xgboost_mcft": float(with_rain),
                "baseline_error_mcft": float(baseline - actual),
                "rainfall_error_mcft": float(with_rain - actual),
                "antecedent_rainfall_30d_mm": float(antecedent_30d.sum())
                if len(antecedent_30d) == 30 else np.nan,
                "rainfall_source": "IMD 0.25-degree daily gridded rainfall",
            })

    predictions = pd.DataFrame(result_rows)
    if predictions.empty:
        raise RuntimeError("No comparable exact-target pairs were available for this window.")

    summaries: list[dict[str, object]] = []
    for lake in CAPACITY:
        subset = predictions[predictions["reservoir"] == lake]
        if not subset.empty:
            summaries.append(_metrics(subset, lake, "rainfall_s2_s1_xgboost_mcft"))
    summaries.append(_metrics(predictions, "all reservoirs pooled", "rainfall_s2_s1_xgboost_mcft"))
    summary = pd.DataFrame(summaries)

    prediction_path, summary_path = Path(predictions_path), Path(summary_path)
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    for frame, destination in ((predictions, prediction_path), (summary, summary_path)):
        temporary = destination.with_name(destination.name + ".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(destination)

    if yearly_summary_path is not None:
        yearly_rows: list[dict[str, object]] = []
        predictions["target_year"] = pd.to_datetime(predictions["target_date"]).dt.year
        for year, year_frame in predictions.groupby("target_year", sort=True):
            for lake, lake_frame in year_frame.groupby("reservoir", sort=True):
                yearly_rows.append({
                    "target_year": int(year),
                    **_metrics(lake_frame, str(lake), "rainfall_s2_s1_xgboost_mcft"),
                })
            yearly_rows.append({
                "target_year": int(year),
                **_metrics(year_frame, "all reservoirs pooled", "rainfall_s2_s1_xgboost_mcft"),
            })
        yearly_summary = pd.DataFrame(yearly_rows)
        yearly_destination = Path(yearly_summary_path)
        yearly_destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = yearly_destination.with_name(yearly_destination.name + ".tmp")
        yearly_summary.to_csv(temporary, index=False)
        temporary.replace(yearly_destination)
        print("\nScores by target year:")
        print(yearly_summary.round(2).to_string(index=False))
        print(f"Yearly summary: {yearly_destination.resolve()}")

    print(f"Target window: {start:%Y-%m-%d} through {end:%Y-%m-%d}")
    print("Backtest: current S2-first/S1-fallback XGBoost vs the same model plus prior rainfall")
    print(summary.round(2).to_string(index=False))
    print(f"Prediction pairs: {len(predictions)} -> {prediction_path.resolve()}")
    print(f"Summary: {summary_path.resolve()}")
    print("Only rainfall from dates strictly before each forecast origin was used. This research run does not change the live forecast.")
    return predictions, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", default=DEFAULT_OFFICIAL)
    parser.add_argument("--conflicts", default=DEFAULT_CONFLICTS)
    parser.add_argument("--satellite", default=DEFAULT_SATELLITE)
    parser.add_argument("--base-backtest", default=DEFAULT_BASE_BACKTEST)
    parser.add_argument("--rainfall", default=DEFAULT_RAINFALL)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2019-08-31")
    parser.add_argument("--predictions-output", default=DEFAULT_PREDICTIONS)
    parser.add_argument("--summary-output", default=DEFAULT_SUMMARY)
    parser.add_argument("--yearly-summary-output", help="Optional separate CSV with per-year/per-reservoir scores.")
    args = parser.parse_args()
    run_backtest(
        args.official, args.conflicts, args.satellite, args.base_backtest,
        args.rainfall, args.start_date, args.end_date,
        args.predictions_output, args.summary_output, args.yearly_summary_output,
    )


if __name__ == "__main__":
    main()
