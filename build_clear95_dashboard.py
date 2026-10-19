"""Assemble the active dashboard history and metrics from clear95 S2 OOS rows.

The input estimates must already have been built with the chronological,
prior-only calibration in build_sentinel2_storage.py. This script does not
fit a model or alter official readings; it validates the candidate, combines
it with the current official-history column, and calculates matching OOS
metrics for the accuracy panel.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


RESERVOIRS = ("poondi", "cholavaram", "puzhal", "chembarambakkam", "veeranam")
ALIASES = {"puzhal": ("puzhal", "redhills", "red hills")}
CAPACITY = {
    "poondi": 3231.0,
    "cholavaram": 1081.0,
    "puzhal": 3300.0,
    "chembarambakkam": 3645.0,
    "veeranam": 1465.0,
}


def normalize_lake(value: object) -> str:
    name = str(value).strip().lower().replace("_", " ")
    for lake, aliases in ALIASES.items():
        if any(alias in name for alias in aliases):
            return lake
    for lake in RESERVOIRS:
        if lake in name:
            return lake
    return ""


def read_conflicts(path: Path) -> set[tuple[str, pd.Timestamp]]:
    if not path.exists():
        return set()
    frame = pd.read_csv(path, dtype=str)
    if not {"date", "reservoir"}.issubset(frame.columns):
        raise ValueError("Conflict file must have date and reservoir columns.")
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    lakes = frame["reservoir"].map(normalize_lake)
    return {
        (lake, date)
        for lake, date in zip(lakes, dates)
        if lake in RESERVOIRS and pd.notna(date)
    }


def load_estimates(path: Path, conflicts_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = pd.read_csv(path)
    required = {
        "date", "reservoir", "satellite_estimate_mcft", "official_storage_mcft",
        "estimate_error_mcft", "calibration_method", "clear_area_fraction",
        "image_footprint_fraction", "boundary_version",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Clear95 candidate is missing columns: {', '.join(sorted(missing))}")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].map(normalize_lake)
    for column in (
        "satellite_estimate_mcft", "official_storage_mcft", "clear_area_fraction",
        "image_footprint_fraction", "scene_cloud_percent",
    ):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "scene_cloud_percent" not in frame:
        frame["scene_cloud_percent"] = 100.0
    frame = frame.dropna(subset=["date"])
    frame = frame[
        frame["reservoir"].isin(RESERVOIRS)
        & frame["boundary_version"].astype(str).str.strip().str.lower().eq("v3")
        & (frame["clear_area_fraction"] >= 0.95)
        & (frame["image_footprint_fraction"] >= 0.995)
        & frame["satellite_estimate_mcft"].notna()
        & np.isfinite(frame["satellite_estimate_mcft"])
    ].copy()
    if frame.empty:
        raise ValueError("No quality-passing clear95 estimates were found.")

    methods = frame["calibration_method"].dropna().astype(str).str.lower()
    if not methods.str.contains("earlier exact-date matches only").all():
        raise ValueError("Candidate is not marked as prior-only exact-date calibration.")

    frame = frame.sort_values(
        ["reservoir", "date", "clear_area_fraction", "image_footprint_fraction", "scene_cloud_percent"],
        ascending=[True, True, False, False, True],
        kind="stable",
    ).drop_duplicates(["reservoir", "date"], keep="first")

    conflicts = read_conflicts(conflicts_path)
    scored = frame.dropna(subset=["official_storage_mcft"]).copy()
    if "validation_split" in scored.columns:
        scored = scored[scored["validation_split"].astype(str).str.lower().eq("holdout")].copy()
    scored = scored.loc[
        [(row.reservoir, row.date) not in conflicts for row in scored.itertuples(index=False)]
    ].copy()
    scored["error_mcft"] = scored["satellite_estimate_mcft"] - scored["official_storage_mcft"]
    return frame, scored


def build_metrics(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for lake in RESERVOIRS:
        group = scored[scored["reservoir"] == lake]
        if group.empty:
            continue
        is_threshold_selected = "area_variant" in group.columns
        variant_values = group["area_variant"].dropna().astype(str).unique() if is_threshold_selected else []
        area_variant = variant_values[0] if len(variant_values) == 1 else ""
        errors = group["error_mcft"].to_numpy(dtype=float)
        actual = group["official_storage_mcft"].to_numpy(dtype=float)
        low = actual < 300.0
        low_errors = errors[low]
        rows.append({
            "reservoir": lake,
            "valid_up_to_mcft": "",
            "model": "Expanding isotonic (early-selected S2 threshold)" if is_threshold_selected else "Expanding isotonic (S2 clear95)",
            "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 1),
            "bias": round(float(np.mean(errors)), 1),
            "low_storage_bias": round(float(np.mean(errors[low])), 1) if low.any() else "",
            "n_matched": len(group),
            "cv_method": "later 20% chronological holdout" if is_threshold_selected else "expanding-window chronological",
            "cv_splits": "early-date area-rule selection; prior-only calibration" if is_threshold_selected else "prior-only, per acquisition",
            "validation_source": (
                f"Sentinel-2 v3 {area_variant}; area rule selected on earlier dates; "
                ">=95% clear area; >=99.5% footprint"
                if is_threshold_selected else
                "Sentinel-2 v3 clear95; >=95% clear area; >=99.5% footprint"
            ),
            "area_variant": area_variant,
            "low_storage_pairs": int(low.sum()),
            "low_storage_rmse": round(float(np.sqrt(np.mean(low_errors ** 2))), 1) if low.any() else "",
        })
    return pd.DataFrame(rows)


def build_history(history_path: Path, estimates: pd.DataFrame) -> pd.DataFrame:
    history = pd.read_csv(history_path, parse_dates=["date"])
    required = {"date", "reservoir", "official_mcft"}
    if not required.issubset(history.columns):
        raise ValueError("Existing dashboard history must contain date, reservoir, and official_mcft.")
    history["date"] = pd.to_datetime(history["date"], errors="coerce").dt.normalize()
    history["key"] = history["reservoir"].map(normalize_lake)
    history["official_mcft"] = pd.to_numeric(history["official_mcft"], errors="coerce")
    history = history.dropna(subset=["date"])
    history = history[history["key"].isin(RESERVOIRS)]

    duplicate_labels = history.groupby(["key", "date"])["official_mcft"].nunique(dropna=True)
    if (duplicate_labels > 1).any():
        raise ValueError("Existing dashboard history has conflicting official values on a lake/date.")
    official = history[["key", "date", "official_mcft"]].drop_duplicates(["key", "date"])

    sat = estimates[["reservoir", "date", "satellite_estimate_mcft"]].rename(
        columns={"reservoir": "key", "satellite_estimate_mcft": "sat_estimate_mcft"}
    )
    all_keys = pd.concat(
        [official[["key", "date"]], sat[["key", "date"]]], ignore_index=True
    ).drop_duplicates(["key", "date"])
    result = all_keys.merge(official, on=["key", "date"], how="left", validate="one_to_one")
    result = result.merge(sat, on=["key", "date"], how="left", validate="one_to_one")
    result["reservoir"] = result["key"]
    return result[["reservoir", "date", "sat_estimate_mcft", "official_mcft"]].sort_values(
        ["reservoir", "date"], kind="stable"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimates", default="sentinel2_v3_clear95_oos_storage_candidate.csv")
    parser.add_argument("--history", default="dashboard_data.csv")
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--dashboard-output", default="dashboard_data_clear95_candidate.csv")
    parser.add_argument("--metrics-output", default="metrics_clear95_candidate.csv")
    args = parser.parse_args()

    estimates_path = Path(args.estimates)
    history_path = Path(args.history)
    if not estimates_path.exists() or not history_path.exists():
        raise SystemExit("Clear95 estimates or existing dashboard history file was not found.")

    estimates, scored = load_estimates(estimates_path, Path(args.conflicts))
    history = build_history(history_path, estimates)
    metrics = build_metrics(scored)
    if metrics.empty:
        raise SystemExit("No exact-date rows remain for scoring after conflict exclusion.")

    history_path_out = Path(args.dashboard_output)
    metrics_path_out = Path(args.metrics_output)
    history.to_csv(history_path_out, index=False, date_format="%Y-%m-%d")
    metrics.to_csv(metrics_path_out, index=False)

    print("Clear95 prior-only satellite dashboard candidate")
    print("Official readings are carried forward unchanged; satellite rows use only earlier exact-date labels.")
    print(metrics[[
        "reservoir", "n_matched", "rmse", "bias", "low_storage_rmse", "low_storage_pairs",
    ]].to_string(index=False))
    print(f"Dashboard candidate: {history_path_out.resolve()} ({len(history)} rows)")
    print(f"Metrics candidate: {metrics_path_out.resolve()}")


if __name__ == "__main__":
    main()
