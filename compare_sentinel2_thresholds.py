"""Chronologically compare Sentinel-2 water-index/threshold choices.

Consumes the separate wide CSV exported by gee_extract_v3_threshold_sweep.js.
Only exact reservoir/date/image-id matches from the reviewed clear95 label file
are scored. The current per-lake extraction area is included as the baseline.
Each area variant is calibrated using earlier clean CMWSSB pairs; the first
80% of dates chooses a variant and the final 20% is held out.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


CAPACITY = {
    "poondi": 3231.0,
    "cholavaram": 1081.0,
    "puzhal": 3300.0,
    "chembarambakkam": 3645.0,
    "veeranam": 1465.0,
}
THRESHOLDS = (-0.10, 0.00, 0.10, 0.20, 0.30)
INDICES = ("ndwi", "mndwi")
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


def threshold_token(value: float) -> str:
    hundredths = int(round(abs(value) * 100))
    return ("m" if value < 0 else "p") + f"{hundredths:03d}"


def variant_name(index: str, threshold: float) -> str:
    return f"{index}_{threshold_token(threshold)}"


SWEEP_VARIANTS = tuple(
    variant_name(index, threshold)
    for index in INDICES
    for threshold in THRESHOLDS
)
BASELINE_VARIANT = "current_baseline"
VARIANTS = (BASELINE_VARIANT, *SWEEP_VARIANTS)
AREA_COLUMNS = {
    BASELINE_VARIANT: "current_satellite_area_hectares",
    **{variant: f"{variant}_area_hectares" for variant in SWEEP_VARIANTS},
}
PREDICTION_COLUMNS = {variant: f"{variant}_prediction_mcft" for variant in VARIANTS}


def conflict_keys(path: Path) -> set[tuple[str, str]]:
    conflicts = pd.read_csv(path, dtype=str)
    if not {"date", "reservoir"}.issubset(conflicts.columns):
        raise ValueError("Conflict CSV must include date and reservoir columns.")
    conflicts["date"] = pd.to_datetime(conflicts["date"], errors="coerce").dt.normalize()
    conflicts["reservoir"] = conflicts["reservoir"].map(normalize_lake)
    return {
        (row.reservoir, row.date.strftime("%Y-%m-%d"))
        for row in conflicts.itertuples(index=False)
        if row.reservoir and pd.notna(row.date)
    }


def read_exact_matches(
    candidates_path: Path,
    matches_path: Path,
    conflicts_path: Path,
    minimum_clear: float,
    minimum_footprint: float,
) -> tuple[pd.DataFrame, int, int]:
    candidates = pd.read_csv(candidates_path, dtype={"image_id": str})
    required_candidate = {
        "reservoir", "image_date", "image_id", "boundary_version",
        "clear_area_fraction", "image_footprint_fraction",
        *(AREA_COLUMNS[v] for v in SWEEP_VARIANTS),
    }
    missing = required_candidate.difference(candidates.columns)
    if missing:
        raise ValueError(
            "Threshold-sweep CSV is missing columns: " + ", ".join(sorted(missing))
        )
    candidates["date"] = pd.to_datetime(candidates["image_date"], errors="coerce").dt.normalize()
    candidates["reservoir"] = candidates["reservoir"].map(normalize_lake)
    candidates["image_id"] = candidates["image_id"].fillna("").astype(str).str.strip()
    for column in (
        "clear_area_fraction", "image_footprint_fraction",
        *(AREA_COLUMNS[v] for v in SWEEP_VARIANTS),
    ):
        candidates[column] = pd.to_numeric(candidates[column], errors="coerce")
    candidates = candidates.dropna(subset=["date"])
    candidates = candidates[
        candidates["reservoir"].isin(CAPACITY) &
        (candidates["boundary_version"].astype(str).str.strip().str.lower() == "v3") &
        (candidates["clear_area_fraction"] >= minimum_clear) &
        (candidates["image_footprint_fraction"] >= minimum_footprint) &
        (candidates["image_id"] != "")
    ].copy()
    candidates = candidates.drop_duplicates(["reservoir", "date", "image_id"], keep="first")

    matches = pd.read_csv(matches_path, dtype={"image_id": str})
    required_matches = {
        "date", "reservoir", "image_id", "official_storage_mcft",
        "satellite_area_hectares", "clear_area_fraction",
        "image_footprint_fraction", "boundary_version",
    }
    missing = required_matches.difference(matches.columns)
    if missing:
        raise ValueError("Matched CSV is missing columns: " + ", ".join(sorted(missing)))
    matches["date"] = pd.to_datetime(matches["date"], errors="coerce").dt.normalize()
    matches["reservoir"] = matches["reservoir"].map(normalize_lake)
    matches["image_id"] = matches["image_id"].fillna("").astype(str).str.strip()
    for column in (
        "official_storage_mcft", "satellite_area_hectares",
        "clear_area_fraction", "image_footprint_fraction",
    ):
        matches[column] = pd.to_numeric(matches[column], errors="coerce")
    matches = matches.dropna(subset=["date", "official_storage_mcft", "satellite_area_hectares"])
    matches = matches[
        matches["reservoir"].isin(CAPACITY) &
        (matches["boundary_version"].astype(str).str.strip().str.lower() == "v3") &
        (matches["clear_area_fraction"] >= minimum_clear) &
        (matches["image_footprint_fraction"] >= minimum_footprint) &
        (matches["image_id"] != "")
    ].copy()
    matches = matches[
        matches.apply(
            lambda row: 0.0 <= row["official_storage_mcft"] <= CAPACITY[row["reservoir"]],
            axis=1,
        )
    ]
    matches = matches.sort_values(
        ["reservoir", "date", "clear_area_fraction", "image_footprint_fraction", "image_id"],
        ascending=[True, True, False, False, True],
        kind="stable",
    ).drop_duplicates(["reservoir", "date"], keep="first")

    excluded = conflict_keys(conflicts_path)
    match_keys = list(zip(matches["reservoir"], matches["date"].dt.strftime("%Y-%m-%d")))
    matches = matches.loc[[key not in excluded for key in match_keys]].copy()
    label_count = len(matches)
    labels = matches[[
        "reservoir", "date", "image_id", "official_storage_mcft", "satellite_area_hectares"
    ]].rename(columns={"satellite_area_hectares": AREA_COLUMNS[BASELINE_VARIANT]})
    joined = candidates.merge(
        labels,
        on=["reservoir", "date", "image_id"],
        how="inner",
        validate="one_to_one",
    )
    joined = joined.dropna(subset=list(AREA_COLUMNS.values())).copy()
    for column in AREA_COLUMNS.values():
        joined = joined[joined[column] >= 0]
    joined = joined.sort_values(["reservoir", "date"], kind="stable").reset_index(drop=True)
    return joined, label_count, len(joined)


def add_oos_predictions(
    group: pd.DataFrame,
    minimum_training_pairs: int,
) -> pd.DataFrame:
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError as exc:
        raise SystemExit("scikit-learn is required. Install it with: python -m pip install scikit-learn") from exc

    output = group.copy().sort_values("date").reset_index(drop=True)
    for variant in VARIANTS:
        area_column = AREA_COLUMNS[variant]
        prediction_column = PREDICTION_COLUMNS[variant]
        predictions = np.full(len(output), np.nan, dtype=float)
        for target_index, target in output.iterrows():
            prior = output.iloc[:target_index]
            if (
                len(prior) < minimum_training_pairs or
                prior[area_column].nunique() < 4
            ):
                continue
            model = IsotonicRegression(
                y_min=0.0,
                y_max=CAPACITY[target["reservoir"]],
                out_of_bounds="clip",
            )
            model.fit(prior[area_column].to_numpy(dtype=float), prior["official_storage_mcft"].to_numpy(dtype=float))
            predictions[target_index] = float(model.predict([float(target[area_column])])[0])
        output[prediction_column] = predictions
    return output


def score(frame: pd.DataFrame, variant: str, capacity: float) -> dict[str, float | int]:
    actual = frame["official_storage_mcft"].to_numpy(dtype=float)
    predicted = frame[PREDICTION_COLUMNS[variant]].to_numpy(dtype=float)
    error = predicted - actual
    low = actual < LOW_STORAGE_MCFT
    return {
        "pairs": len(frame),
        "rmse_mcft": float(np.sqrt(np.mean(error ** 2))),
        "rmse_pct_capacity": float(np.sqrt(np.mean(error ** 2)) / capacity * 100),
        "mae_mcft": float(np.mean(np.abs(error))),
        "bias_mcft": float(np.mean(error)),
        "low_pairs": int(np.sum(low)),
        "low_rmse_mcft": float(np.sqrt(np.mean(error[low] ** 2))) if np.any(low) else float("nan"),
    }


def baseline_variant(lake: str) -> str:
    # The accuracy-ready match file carries the exact area used by the
    # existing extraction for this lake (NDWI for four lakes, MNDWI for Chem).
    return BASELINE_VARIANT


def same_scale_baseline_variant(lake: str) -> str:
    # The new sweep measures both indices at 20 m. Compare each selected
    # method to the current index/threshold reproduced at that same scale.
    index = "mndwi" if lake == "chembarambakkam" else "ndwi"
    return variant_name(index, 0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, help="CSV exported by gee_extract_v3_threshold_sweep.js.")
    parser.add_argument("--matches", default="sentinel2_v3_clear95_accuracy_ready.csv")
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--output", default="sentinel2_v3_clear95_threshold_comparison_candidate.csv")
    parser.add_argument("--minimum-clear", type=float, default=0.95)
    parser.add_argument("--minimum-footprint", type=float, default=0.995)
    parser.add_argument("--minimum-training-pairs", type=int, default=8)
    parser.add_argument("--holdout-fraction", type=float, default=0.20)
    args = parser.parse_args()

    for path in (Path(args.candidates), Path(args.matches), Path(args.conflicts)):
        if not path.exists():
            raise SystemExit(f"Required input file not found: {path.resolve()}")
    if not 0.1 <= args.holdout_fraction <= 0.5:
        raise SystemExit("--holdout-fraction must be between 0.1 and 0.5.")

    frame, label_count, joined_count = read_exact_matches(
        Path(args.candidates), Path(args.matches), Path(args.conflicts),
        args.minimum_clear, args.minimum_footprint,
    )
    print(f"Clean exact-date labels after conflict exclusion: {label_count}")
    print(f"Labels joined to the same Earth Engine image ID: {joined_count}")
    if joined_count == 0:
        raise SystemExit("No exact scene matches joined. Check image_id/date fields in both CSVs.")

    all_predictions: list[pd.DataFrame] = []
    summaries: list[dict[str, object]] = []
    selected_by_lake: dict[str, str] = {}

    for lake, capacity in CAPACITY.items():
        group = frame[frame["reservoir"] == lake].sort_values("date").reset_index(drop=True)
        if len(group) < args.minimum_training_pairs + 10:
            print(f"{lake}: only {len(group)} exact matches; too few for a threshold comparison.")
            continue
        split_index = int(np.floor(len(group) * (1.0 - args.holdout_fraction)))
        split_index = max(args.minimum_training_pairs + 1, min(split_index, len(group) - 1))
        cutoff = pd.Timestamp(group.iloc[split_index]["date"])
        predicted = add_oos_predictions(group, args.minimum_training_pairs)
        predicted["split"] = np.where(predicted["date"] < cutoff, "selection", "holdout")

        selection = predicted[predicted["split"] == "selection"]
        holdout = predicted[predicted["split"] == "holdout"]
        eligible = [
            variant for variant in VARIANTS
            if selection[PREDICTION_COLUMNS[variant]].notna().sum() >= 10
        ]
        if not eligible:
            print(f"{lake}: no threshold variant has 10 early-date predictions.")
            continue
        selection_common = selection.dropna(subset=[PREDICTION_COLUMNS[v] for v in eligible])
        if len(selection_common) < 10:
            print(f"{lake}: only {len(selection_common)} common early-date predictions; cannot select fairly.")
            continue

        selection_scores = {
            variant: score(selection_common, variant, capacity)
            for variant in eligible
        }
        selected = min(eligible, key=lambda variant: selection_scores[variant]["rmse_mcft"])
        selected_by_lake[lake] = selected
        predicted["selected_from_early_dates"] = selected
        all_predictions.append(predicted)

        holdout_common = holdout.dropna(subset=[PREDICTION_COLUMNS[v] for v in eligible])
        baseline = baseline_variant(lake)
        print(
            f"\n{lake}: selected {selected} from {len(selection_common)} common early dates; "
            f"holdout starts {cutoff.date()} ({len(holdout_common)} common later dates)"
        )
        same_scale_baseline = same_scale_baseline_variant(lake)
        comparison_variants = list(dict.fromkeys([
            selected, same_scale_baseline, baseline,
        ]))
        for split_name, subset in (("selection", selection_common), ("holdout", holdout_common)):
            for variant in comparison_variants:
                if variant not in eligible or subset.empty:
                    continue
                result = score(subset, variant, capacity)
                summaries.append({
                    "reservoir": lake,
                    "split": split_name,
                    "variant": variant,
                    "common_dates": len(subset),
                    **result,
                })
                if split_name == "holdout":
                    print(
                        f"  {variant}: RMSE {result['rmse_mcft']:.1f} mcft "
                        f"({result['rmse_pct_capacity']:.1f}% capacity), "
                        f"MAE {result['mae_mcft']:.1f}, bias {result['bias_mcft']:+.1f}, "
                        f"low-storage pairs {result['low_pairs']}"
                    )

    if not all_predictions:
        raise SystemExit("No lake had enough exact-date matches for the comparison.")
    output_path = Path(args.output)
    pd.concat(all_predictions, ignore_index=True).to_csv(output_path, index=False)
    print("\nSelected variant, same-scale baseline, and current baseline (same dates)")
    print(pd.DataFrame(summaries).to_string(index=False, formatters={
        "rmse_mcft": "{:.1f}".format,
        "rmse_pct_capacity": "{:.1f}%".format,
        "mae_mcft": "{:.1f}".format,
        "bias_mcft": "{:+.1f}".format,
        "low_rmse_mcft": "{:.1f}".format,
    }))
    print(f"Selected variants by lake: {selected_by_lake}")
    print("Choose using selection dates only; the later holdout is for final evaluation.")
    print(f"Candidate predictions: {output_path.resolve()}")


if __name__ == "__main__":
    main()
