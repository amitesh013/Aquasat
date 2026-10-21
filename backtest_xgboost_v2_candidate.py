"""Quarterly rolling-origin comparison for the pooled v2 forecast candidate.

Reads existing baseline forecast rows so v2 is scored on the exact same dates,
actual readings, and conflict exclusions. The model settings are frozen to the
reported Claude configuration. Output files are research candidates only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_15day_forecasts import CAPACITY, read_official_series
from xgboost_forecasting import read_satellite_histories
import aquasat_forecast_v2_candidate as v2


def _read_baselines(paths: list[str]) -> pd.DataFrame:
    frames = []
    required = {
        "reservoir", "cutoff_date", "origin_date", "target_date", "lead_days",
        "actual_mcft", "xgboost_prediction_mcft",
    }
    for name in paths:
        path = Path(name)
        if not path.exists():
            raise FileNotFoundError(f"Baseline forecast file not found: {path.resolve()}")
        frame = pd.read_csv(path)
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"{path} lacks columns: {', '.join(sorted(missing))}")
        frames.append(frame)
    baseline = pd.concat(frames, ignore_index=True)
    baseline["reservoir"] = baseline["reservoir"].astype(str).str.strip().str.lower()
    for column in ("cutoff_date", "origin_date", "target_date"):
        baseline[column] = pd.to_datetime(baseline[column], errors="coerce").dt.normalize()
    for column in (
        "lead_days", "actual_mcft", "xgboost_prediction_mcft",
        "xgboost_satellite_prediction_mcft",
    ):
        if column not in baseline:
            baseline[column] = np.nan
        baseline[column] = pd.to_numeric(baseline[column], errors="coerce")
    baseline = baseline.dropna(subset=[
        "reservoir", "cutoff_date", "origin_date", "target_date", "lead_days", "actual_mcft",
        "xgboost_prediction_mcft",
    ])
    baseline = baseline[baseline["lead_days"].between(12, 18)].copy()
    keys = ["reservoir", "origin_date", "target_date"]
    duplicated = baseline[baseline.duplicated(keys, keep=False)]
    if not duplicated.empty:
        prediction_cols = ["actual_mcft", "xgboost_prediction_mcft", "xgboost_satellite_prediction_mcft"]
        for _, group in duplicated.groupby(keys, dropna=False):
            if any(group[column].nunique(dropna=False) > 1 for column in prediction_cols):
                raise ValueError("Baseline files contain conflicting predictions for duplicate forecast rows.")
        baseline = baseline.drop_duplicates(keys, keep="last")
    return baseline.sort_values(["origin_date", "reservoir"]).reset_index(drop=True)


def _summary_rows(frame: pd.DataFrame, holdout_start: pd.Timestamp) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    predictions = [
        ("baseline_xgb_storage", "xgboost_prediction_mcft"),
        ("baseline_xgb_satellite", "xgboost_satellite_prediction_mcft"),
        ("v2_pooled", "v2_prediction_mcft"),
    ]
    if "v2_satellite_prediction_mcft" in frame:
        predictions.append(("v2_pooled_plus_satellite", "v2_satellite_prediction_mcft"))
    if "v2_rainfall_prediction_mcft" in frame:
        predictions.append(("v2_pooled_plus_rainfall", "v2_rainfall_prediction_mcft"))
    frame = frame.copy()
    frame["evaluation_period"] = np.where(
        frame["target_date"] >= holdout_start,
        "locked_holdout",
        "development_and_reported_tuning_period",
    )
    groups = [("all", frame)]
    groups.extend((lake, group) for lake, group in frame.groupby("reservoir", sort=True))
    for period, period_frame in frame.groupby("evaluation_period", sort=False):
        groups.append((period, period_frame))
        groups.extend(
            (f"{period}:{lake}", group)
            for lake, group in period_frame.groupby("reservoir", sort=True)
        )
    for label, group in groups:
        if group.empty:
            continue
        for method, column in predictions:
            if column not in group:
                continue
            valid = group[column].notna() & group["actual_mcft"].notna()
            if not valid.any():
                continue
            error = group.loc[valid, column] - group.loc[valid, "actual_mcft"]
            pct_error = error / group.loc[valid, "capacity_mcft"] * 100.0
            rows.append({
                "group": label,
                "method": method,
                "pairs": int(valid.sum()),
                "rmse_mcft": float(np.sqrt(np.mean(np.square(error)))),
                "rmse_pct_capacity": float(np.sqrt(np.mean(np.square(pct_error)))),
                "mae_mcft": float(np.mean(np.abs(error))),
                "bias_mcft": float(np.mean(error)),
            })
    return pd.DataFrame(rows)


def run_backtest(
    baseline_paths: list[str],
    official_path: str,
    conflicts_path: str,
    rainfall_path: str | None,
    start_date: str | None,
    end_date: str | None,
    holdout_start: str,
    predictions_output: str,
    summary_output: str,
    rainfall_windows: tuple[int, ...] | None = None,
    development_only: bool = False,
    satellite_path: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    holdout = pd.Timestamp(holdout_start)
    selected_rainfall_windows = tuple(
        v2.RAINFALL_WINDOWS if rainfall_windows is None else rainfall_windows
    )
    if (
        not selected_rainfall_windows
        or len(set(selected_rainfall_windows)) != len(selected_rainfall_windows)
        or any(window not in v2.RAINFALL_WINDOWS for window in selected_rainfall_windows)
    ):
        raise ValueError(
            f"Rainfall windows must be a unique non-empty subset of {v2.RAINFALL_WINDOWS}."
        )
    official, excluded = read_official_series(Path(official_path), Path(conflicts_path))
    baseline = _read_baselines(baseline_paths)
    if start_date:
        baseline = baseline[baseline["target_date"] >= pd.Timestamp(start_date)]
    if end_date:
        baseline = baseline[baseline["target_date"] <= pd.Timestamp(end_date)]
    if development_only:
        baseline = baseline[baseline["target_date"] < holdout]
    if baseline.empty:
        raise ValueError("No baseline forecast rows remain in the requested target-date range.")

    # Re-check every target against the same conflict-filtered official source.
    keep = []
    for row in baseline.itertuples(index=False):
        lake = row.reservoir
        if lake not in official or pd.Timestamp(row.target_date) not in official[lake].index:
            raise ValueError(
                f"Baseline target {lake} {row.target_date:%Y-%m-%d} is missing or disputed in official data."
            )
        actual = float(official[lake].loc[pd.Timestamp(row.target_date)])
        if not np.isclose(actual, float(row.actual_mcft), rtol=0, atol=1e-6):
            raise ValueError(
                f"Actual mismatch for {lake} {row.target_date:%Y-%m-%d}: "
                f"baseline={row.actual_mcft}, official={actual}."
            )
        if lake not in CAPACITY:
            raise ValueError(f"Unknown reservoir in baseline: {lake}")
        keep.append(True)
    baseline = baseline.loc[keep].copy().reset_index(drop=True)
    baseline["actual_mcft"] = [
        float(official[row.reservoir].loc[row.target_date])
        for row in baseline.itertuples(index=False)
    ]
    baseline["capacity_mcft"] = baseline["reservoir"].map(CAPACITY)

    rainfall = v2.read_rainfall_histories(rainfall_path) if rainfall_path else None
    satellite = (
        read_satellite_histories(Path(satellite_path), CAPACITY, excluded_pairs=excluded)
        if satellite_path else None
    )
    if satellite_path and not satellite:
        raise ValueError(f"No satellite histories could be loaded from {satellite_path}.")
    if rainfall is not None:
        # Every selected rainfall feature needs a complete antecedent window.
        for row in baseline.itertuples(index=False):
            for days in selected_rainfall_windows:
                amount = v2._rainfall_total(rainfall[row.reservoir], row.origin_date, days)
                if not np.isfinite(amount):
                    raise ValueError(
                        f"Incomplete {days}-day rainfall history for {row.reservoir} "
                        f"before {row.origin_date:%Y-%m-%d}; the selected rainfall source "
                        "does not cover every scored origin with daily values."
                    )

    raw, filled = v2.daily_fraction_frame(official)
    no_rain_table = v2.build_feature_table(raw, filled)
    rain_table = (
        v2.build_feature_table(
            raw,
            filled,
            rainfall_histories=rainfall,
            rainfall_windows=selected_rainfall_windows,
        )
        if rainfall is not None else None
    )
    satellite_table = (
        v2.build_feature_table(raw, filled, satellite_histories=satellite)
        if satellite is not None else None
    )
    baseline["v2_prediction_mcft"] = np.nan
    if rainfall is not None:
        baseline["v2_rainfall_prediction_mcft"] = np.nan
    if satellite is not None:
        baseline["v2_satellite_prediction_mcft"] = np.nan
    baseline["v2_training_rows"] = 0
    baseline["v2_fit_origin"] = ""

    # Match Claude's experimental driver: group by cutoff quarter and fit once
    # using the earliest available forecast origin in each quarter.
    quarter_groups = baseline.groupby(baseline["cutoff_date"].dt.to_period("Q"), sort=True)
    for quarter, indices in quarter_groups.groups.items():
        indices = list(indices)
        earliest_origin = baseline.loc[indices, "origin_date"].min()
        model, columns, training_count = v2.fit_model(no_rain_table, earliest_origin)
        rain_model = None
        rain_columns: list[str] | None = None
        satellite_model = None
        satellite_columns: list[str] | None = None
        if rain_table is not None:
            rain_model, rain_columns, _ = v2.fit_model(rain_table, earliest_origin)
        if satellite_table is not None:
            satellite_model, satellite_columns, _ = v2.fit_model(
                satellite_table, earliest_origin
            )
        print(
            f"Fitting v2 for {quarter}: origin {earliest_origin:%Y-%m-%d}, "
            f"{training_count:,} training rows; scoring {len(indices)} targets.",
            flush=True,
        )
        for index in indices:
            row = baseline.loc[index]
            lake, origin, target = row["reservoir"], row["origin_date"], row["target_date"]
            baseline.at[index, "v2_prediction_mcft"] = v2.predict_storage(
                model, columns, raw, filled, lake, origin, target
            )
            if rain_model is not None and rain_columns is not None and rainfall is not None:
                baseline.at[index, "v2_rainfall_prediction_mcft"] = v2.predict_storage(
                    rain_model,
                    rain_columns,
                    raw,
                    filled,
                    lake,
                    origin,
                    target,
                    rainfall,
                    selected_rainfall_windows,
                )
            if (
                satellite_model is not None
                and satellite_columns is not None
                and satellite is not None
            ):
                baseline.at[index, "v2_satellite_prediction_mcft"] = v2.predict_storage(
                    satellite_model,
                    satellite_columns,
                    raw,
                    filled,
                    lake,
                    origin,
                    target,
                    satellite_histories=satellite,
                )
            baseline.at[index, "v2_training_rows"] = training_count
            baseline.at[index, "v2_fit_origin"] = earliest_origin.strftime("%Y-%m-%d")

    baseline["v2_error_mcft"] = baseline["v2_prediction_mcft"] - baseline["actual_mcft"]
    if rainfall is not None:
        baseline["v2_rainfall_error_mcft"] = (
            baseline["v2_rainfall_prediction_mcft"] - baseline["actual_mcft"]
        )
    if satellite is not None:
        baseline["v2_satellite_error_mcft"] = (
            baseline["v2_satellite_prediction_mcft"] - baseline["actual_mcft"]
        )
    summary = _summary_rows(baseline, holdout)

    prediction_path = Path(predictions_output)
    summary_path = Path(summary_output)
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    baseline.to_csv(prediction_path, index=False, date_format="%Y-%m-%d")
    summary.to_csv(summary_path, index=False)
    print(f"Excluded disputed lake/date pairs in source: {len(excluded)}")
    print(f"Matched forecast/actual pairs: {len(baseline)}")
    print(f"Locked holdout starts at target date {holdout:%Y-%m-%d}.")
    if rainfall is not None:
        print(f"Rainfall windows: {', '.join(f'{days}d' for days in selected_rainfall_windows)}")
    if satellite is not None:
        print(
            f"Satellite histories loaded for {len(satellite)} reservoirs from {satellite_path}; "
            "features use the latest estimate on or before each anchor."
        )
    if development_only:
        print("Development-only scoring: holdout targets were excluded from fitting and metrics.")
    elif not (baseline["target_date"] >= holdout).any():
        print("WARNING: no locked-holdout rows were supplied; extend the baseline through October 2026.")
    print(summary.round(3).to_string(index=False))
    print(f"Predictions: {prediction_path.resolve()}")
    print(f"Summary: {summary_path.resolve()}")
    return baseline, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", nargs="+", default=["forecast_backtest_s2_s1_candidate.csv"],
        help="One or more saved baseline backtests with XGBoost prediction columns.",
    )
    parser.add_argument(
        "--official", default="reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv"
    )
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--rainfall", help="Optional daily nearest-cell IMD rainfall CSV.")
    parser.add_argument(
        "--satellite",
        help="Optional OOS Sentinel-2-first/Sentinel-1-gap-fill storage history CSV.",
    )
    parser.add_argument(
        "--rainfall-windows", nargs="+", type=int, choices=v2.RAINFALL_WINDOWS,
        help="Rainfall accumulation windows to include (default: 7 30 90).",
    )
    parser.add_argument(
        "--development-only", action="store_true",
        help="Exclude target dates on/after --holdout-start, for feature experiments.",
    )
    parser.add_argument("--start-date", help="Optional inclusive target-date filter.")
    parser.add_argument("--end-date", help="Optional inclusive target-date filter.")
    parser.add_argument(
        "--holdout-start", default="2026-09-01",
        help="Target dates from here onward are reported separately as the locked holdout.",
    )
    parser.add_argument("--predictions-output", default="forecast_backtest_xgb_v2_candidate.csv")
    parser.add_argument("--summary-output", default="forecast_backtest_xgb_v2_summary_candidate.csv")
    args = parser.parse_args()
    run_backtest(
        args.baseline, args.official, args.conflicts, args.rainfall,
        args.start_date, args.end_date, args.holdout_start,
        args.predictions_output, args.summary_output,
        rainfall_windows=tuple(args.rainfall_windows) if args.rainfall_windows else None,
        development_only=args.development_only,
        satellite_path=args.satellite,
    )


if __name__ == "__main__":
    main()
