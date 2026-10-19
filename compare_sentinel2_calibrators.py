"""Compare Sentinel-2 area-to-storage calibrators with a chronological holdout.

Every prediction for a matched image is fitted only on earlier exact-date
CMWSSB pairs. The first 80% of dates select a calibrator per lake; the latest
20% are held out to estimate how that choice generalizes. Disputed lake/date
pairs and scenes below the clear/coverage thresholds are excluded.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit


CAPACITY = {
    "poondi": 3231.0,
    "cholavaram": 1081.0,
    "puzhal": 3300.0,
    "chembarambakkam": 3645.0,
    "veeranam": 1465.0,
}
METHODS = ("isotonic", "power_law", "offset_power_law", "gaussian_process")
LOW_STORAGE_MCFT = 300.0


def normalize_lake(value: object) -> str:
    raw = str(value).strip().lower().replace("_", " ")
    if "poondi" in raw:
        return "poondi"
    if "cholavaram" in raw:
        return "cholavaram"
    if "puzhal" in raw or "redhill" in raw or "red hills" in raw:
        return "puzhal"
    if "chembarambakkam" in raw or "chembaram" in raw:
        return "chembarambakkam"
    if "veeranam" in raw:
        return "veeranam"
    return ""


def power_law(area: np.ndarray, a: float, b: float) -> np.ndarray:
    return a * np.power(np.maximum(area, 1e-6), b)


def offset_power_law(area: np.ndarray, a: float, b: float, offset: float) -> np.ndarray:
    return a * np.power(np.clip(area - offset, 0.0, None), b)


def fit_predict(method: str, train: pd.DataFrame, test_area: float, capacity: float) -> float | None:
    x = train["satellite_area_hectares"].to_numpy(dtype=float)
    y = train["official_storage_mcft"].to_numpy(dtype=float)
    if len(x) < 8 or np.unique(x).size < 4:
        return None
    try:
        if method == "isotonic":
            from sklearn.isotonic import IsotonicRegression

            model = IsotonicRegression(y_min=0.0, y_max=capacity, out_of_bounds="clip")
            model.fit(x, y)
            estimate = float(model.predict([test_area])[0])
        elif method == "power_law":
            params, _ = curve_fit(
                power_law,
                x,
                y,
                p0=[0.004, 1.7],
                bounds=([0.0, 0.1], [np.inf, 5.0]),
                maxfev=50000,
            )
            estimate = float(power_law(np.asarray([test_area]), *params)[0])
        elif method == "offset_power_law":
            base_params, _ = curve_fit(
                power_law,
                x,
                y,
                p0=[0.004, 1.7],
                bounds=([0.0, 0.1], [np.inf, 5.0]),
                maxfev=50000,
            )
            offset_upper = max(float(np.percentile(x, 60)), 1.0)
            offset_start = min(max(float(x.min()) * 0.5, 0.01), offset_upper * 0.5)
            params, _ = curve_fit(
                offset_power_law,
                x,
                y,
                p0=[float(base_params[0]), float(base_params[1]), offset_start],
                bounds=([0.0, 0.1, 0.0], [np.inf, 5.0, offset_upper]),
                maxfev=50000,
            )
            estimate = float(offset_power_law(np.asarray([test_area]), *params)[0])
        elif method == "gaussian_process":
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel

            mean = float(x.mean())
            scale = float(x.std()) or 1.0
            kernel = ConstantKernel(1.0) * RBF(length_scale=1.0, length_scale_bounds=(0.1, 10.0)) + WhiteKernel(
                noise_level=0.1, noise_level_bounds=(1e-3, 1.0)
            )
            model = GaussianProcessRegressor(
                kernel=kernel,
                normalize_y=True,
                n_restarts_optimizer=0,
                random_state=42,
            )
            model.fit(((x - mean) / scale).reshape(-1, 1), y)
            estimate = float(model.predict(np.asarray([[(test_area - mean) / scale]]))[0])
        else:
            raise ValueError(f"Unknown calibration method: {method}")
    except (RuntimeError, ValueError, FloatingPointError, OverflowError):
        return None
    if not np.isfinite(estimate):
        return None
    return float(np.clip(estimate, 0.0, capacity))


def read_matches(path: Path, conflicts_path: Path, minimum_clear: float, minimum_footprint: float) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "date", "reservoir", "official_storage_mcft", "satellite_area_hectares",
        "clear_area_fraction", "image_footprint_fraction", "boundary_version",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Matched CSV is missing columns: {', '.join(sorted(missing))}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].map(normalize_lake)
    for column in ("official_storage_mcft", "satellite_area_hectares", "clear_area_fraction", "image_footprint_fraction"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["scene_cloud_percent"] = pd.to_numeric(
        frame.get("scene_cloud_percent", pd.Series(0.0, index=frame.index)), errors="coerce"
    ).fillna(100.0)
    frame = frame.dropna(subset=["date", "official_storage_mcft", "satellite_area_hectares"])
    frame = frame[
        frame["reservoir"].isin(CAPACITY) &
        (frame["boundary_version"].astype(str).str.strip().str.lower() == "v3") &
        (frame["clear_area_fraction"] >= minimum_clear) &
        (frame["image_footprint_fraction"] >= minimum_footprint) &
        (frame["satellite_area_hectares"] > 0)
    ].copy()
    frame = frame[
        frame.apply(lambda row: 0.0 <= row["official_storage_mcft"] <= CAPACITY[row["reservoir"]], axis=1)
    ]

    conflicts = pd.read_csv(conflicts_path, dtype=str)
    if not {"date", "reservoir"}.issubset(conflicts.columns):
        raise ValueError("Conflict CSV must contain date and reservoir columns.")
    conflict_dates = pd.to_datetime(conflicts["date"], errors="coerce").dt.normalize()
    conflict_lakes = conflicts["reservoir"].map(normalize_lake)
    conflict_keys = {
        (lake, date.strftime("%Y-%m-%d"))
        for lake, date in zip(conflict_lakes, conflict_dates)
        if lake and pd.notna(date)
    }
    keys = list(zip(frame["reservoir"], frame["date"].dt.strftime("%Y-%m-%d")))
    frame = frame.loc[[key not in conflict_keys for key in keys]].copy()
    if "image_id" not in frame:
        frame["image_id"] = ""
    return (
        frame.sort_values(
            ["reservoir", "date", "clear_area_fraction", "image_footprint_fraction", "scene_cloud_percent", "image_id"],
            ascending=[True, True, False, False, True, True],
            kind="stable",
        )
        .drop_duplicates(["reservoir", "date"], keep="first")
        .reset_index(drop=True)
    )


def metrics(frame: pd.DataFrame, prediction_column: str, capacity: float) -> dict[str, float | int]:
    valid = frame.dropna(subset=[prediction_column, "official_storage_mcft"])
    errors = valid[prediction_column].to_numpy(dtype=float) - valid["official_storage_mcft"].to_numpy(dtype=float)
    low = valid[valid["official_storage_mcft"] < LOW_STORAGE_MCFT]
    low_errors = low[prediction_column].to_numpy(dtype=float) - low["official_storage_mcft"].to_numpy(dtype=float)
    return {
        "pairs": len(errors),
        "rmse_mcft": float(np.sqrt(np.mean(errors ** 2))) if len(errors) else float("nan"),
        "rmse_pct_capacity": float(np.sqrt(np.mean(errors ** 2)) / capacity * 100) if len(errors) else float("nan"),
        "mae_mcft": float(np.mean(np.abs(errors))) if len(errors) else float("nan"),
        "bias_mcft": float(np.mean(errors)) if len(errors) else float("nan"),
        "low_pairs": len(low_errors),
        "low_rmse_mcft": float(np.sqrt(np.mean(low_errors ** 2))) if len(low_errors) else float("nan"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches", default="sentinel2_v3_clear95_accuracy_ready.csv")
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--output", default="sentinel2_v3_clear95_calibrator_comparison_candidate.csv")
    parser.add_argument("--minimum-clear", type=float, default=0.95)
    parser.add_argument("--minimum-footprint", type=float, default=0.995)
    parser.add_argument("--minimum-training-pairs", type=int, default=8)
    parser.add_argument("--holdout-fraction", type=float, default=0.20)
    args = parser.parse_args()

    matches_path = Path(args.matches)
    conflicts_path = Path(args.conflicts)
    for path in (matches_path, conflicts_path):
        if not path.exists():
            raise SystemExit(f"Required input file not found: {path.resolve()}")
    if not 0.1 <= args.holdout_fraction <= 0.5:
        raise SystemExit("--holdout-fraction must be between 0.1 and 0.5.")

    frame = read_matches(matches_path, conflicts_path, args.minimum_clear, args.minimum_footprint)
    prediction_rows: list[dict[str, object]] = []
    selected_methods: dict[str, str] = {}
    summaries: list[dict[str, object]] = []

    for lake, capacity in CAPACITY.items():
        group = frame[frame["reservoir"] == lake].sort_values("date").reset_index(drop=True)
        if len(group) < args.minimum_training_pairs + 10:
            print(f"{lake}: only {len(group)} clean pairs; too few for a reliable calibration comparison.")
            continue
        split_index = int(np.floor(len(group) * (1.0 - args.holdout_fraction)))
        split_index = max(args.minimum_training_pairs + 1, min(split_index, len(group) - 1))
        cutoff = pd.Timestamp(group.iloc[split_index]["date"])
        lake_rows: list[dict[str, object]] = []

        for target_index, target in group.iterrows():
            prior = group.iloc[:target_index]
            if len(prior) < args.minimum_training_pairs or prior["satellite_area_hectares"].nunique() < 4:
                continue
            row: dict[str, object] = {
                "reservoir": lake,
                "date": pd.Timestamp(target["date"]).strftime("%Y-%m-%d"),
                "split": "selection" if target["date"] < cutoff else "holdout",
                "official_storage_mcft": float(target["official_storage_mcft"]),
                "training_pairs": len(prior),
                "training_through_date": pd.Timestamp(prior["date"].max()).strftime("%Y-%m-%d"),
                "clear_area_fraction": float(target["clear_area_fraction"]),
                "image_footprint_fraction": float(target["image_footprint_fraction"]),
            }
            for method in METHODS:
                row[f"{method}_prediction_mcft"] = fit_predict(
                    method, prior, float(target["satellite_area_hectares"]), capacity
                )
            lake_rows.append(row)

        if not lake_rows:
            print(f"{lake}: no rows could be scored after the prior-data requirement.")
            continue
        lake_output = pd.DataFrame(lake_rows)
        selection = lake_output[lake_output["split"] == "selection"]
        holdout = lake_output[lake_output["split"] == "holdout"]
        prediction_columns = [f"{method}_prediction_mcft" for method in METHODS]
        # Compare calibrators on identical dates. Otherwise a method that fails
        # on difficult cases could appear better simply because it scored fewer.
        selection_common = selection.dropna(subset=prediction_columns)
        holdout_common = holdout.dropna(subset=prediction_columns)
        if len(selection_common) < 10:
            print(
                f"{lake}: only {len(selection_common)} common selection dates across all methods; "
                "cannot make a fair method choice."
            )
            continue
        selection_scores = {
            method: metrics(selection_common, f"{method}_prediction_mcft", capacity)
            for method in METHODS
        }
        selected = min(METHODS, key=lambda method: selection_scores[method]["rmse_mcft"])
        selected_methods[lake] = selected
        lake_output["selected_from_early_dates"] = selected
        prediction_rows.extend(lake_output.to_dict(orient="records"))

        print(
            f"\n{lake}: method chosen on {len(selection_common)} common early dates = {selected}; "
            f"holdout begins {cutoff.date()} ({len(holdout_common)} common later dates)"
        )
        for split_name, subset in (("selection", selection_common), ("holdout", holdout_common)):
            for method in METHODS:
                score = metrics(subset, f"{method}_prediction_mcft", capacity)
                if score["pairs"] == 0:
                    continue
                summaries.append({
                    "reservoir": lake,
                    "split": split_name,
                    "method": method,
                    "common_dates": len(subset),
                    **score,
                })
                if split_name == "holdout" and method == selected:
                    print(
                        f"  chosen model holdout: {score['pairs']} pairs, "
                        f"RMSE {score['rmse_mcft']:.1f} mcft ({score['rmse_pct_capacity']:.1f}% capacity), "
                        f"MAE {score['mae_mcft']:.1f}, bias {score['bias_mcft']:+.1f}, "
                        f"low-storage RMSE {score['low_rmse_mcft']:.1f} mcft ({score['low_pairs']} pairs)"
                    )

    if not prediction_rows:
        raise SystemExit("No calibration comparison rows were produced. Check clean matches and quality filters.")

    output_path = Path(args.output)
    pd.DataFrame(prediction_rows).to_csv(output_path, index=False)
    summary = pd.DataFrame(summaries)
    print("\nChronological calibration comparison on common dates (selection and untouched later holdout)")
    print(summary.to_string(index=False, formatters={
        "rmse_mcft": "{:.1f}".format,
        "rmse_pct_capacity": "{:.1f}%".format,
        "mae_mcft": "{:.1f}".format,
        "bias_mcft": "{:+.1f}".format,
        "low_rmse_mcft": "{:.1f}".format,
    }))
    print("\nModel choice is based only on the early selection dates; use the later holdout to judge it.")
    print(f"Selected methods by lake: {selected_methods}")
    print(f"Candidate predictions: {output_path.resolve()}")


if __name__ == "__main__":
    main()
