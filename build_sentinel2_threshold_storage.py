"""Build a full-scene Sentinel-2 storage candidate from early-selected thresholds.

The per-reservoir NDWI/MNDWI variants are read from the output of
compare_sentinel2_thresholds.py. Each storage estimate is calibrated only on
earlier exact-scene official matches. Outputs are research candidates.
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
AREA_VARIANTS = {
    f"{index}_{token}_area_hectares": f"{index}_{token}"
    for index in ("ndwi", "mndwi")
    for token in ("m010", "p000", "p010", "p020", "p030")
}


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


def conflict_keys(path: Path) -> set[tuple[str, pd.Timestamp]]:
    frame = pd.read_csv(path, dtype=str)
    if not {"date", "reservoir"}.issubset(frame.columns):
        raise ValueError("Conflict CSV must contain date and reservoir columns.")
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    lakes = frame["reservoir"].map(normalize_lake)
    return {
        (lake, date)
        for lake, date in zip(lakes, dates)
        if lake in CAPACITY and pd.notna(date)
    }


def chosen_variants(path: Path) -> dict[str, str]:
    report = pd.read_csv(path)
    required = {"reservoir", "selected_from_early_dates"}
    if not required.issubset(report.columns):
        raise ValueError("Selection report must come from compare_sentinel2_thresholds.py.")
    report["reservoir"] = report["reservoir"].map(normalize_lake)
    result: dict[str, str] = {}
    for lake, group in report.groupby("reservoir"):
        variants = group["selected_from_early_dates"].dropna().astype(str).unique()
        if lake in CAPACITY and len(variants) == 1:
            result[lake] = variants[0]
        elif lake in CAPACITY:
            raise ValueError(f"Selection report has ambiguous early-date choices for {lake}.")
    missing = set(CAPACITY).difference(result)
    if missing:
        raise ValueError(f"Selection report is missing early-date choices for: {', '.join(sorted(missing))}")
    unknown = set(result.values()).difference(AREA_VARIANTS.values())
    if unknown:
        raise ValueError(f"Selection report names unsupported area variants: {', '.join(sorted(unknown))}")
    return result


def selection_cutoffs(path: Path) -> dict[str, pd.Timestamp]:
    report = pd.read_csv(path, parse_dates=["date"])
    if not {"reservoir", "date", "split"}.issubset(report.columns):
        raise ValueError("Selection report must include reservoir, date, and split columns.")
    report["reservoir"] = report["reservoir"].map(normalize_lake)
    holdout = report[report["split"].astype(str).str.lower().eq("holdout")]
    cutoffs = holdout.groupby("reservoir")["date"].min().to_dict()
    missing = set(CAPACITY).difference(cutoffs)
    if missing:
        raise ValueError(f"Selection report has no later holdout cutoff for: {', '.join(sorted(missing))}")
    return {lake: pd.Timestamp(date).normalize() for lake, date in cutoffs.items()}


def load_scenes(path: Path, variants: dict[str, str], minimum_clear: float, minimum_footprint: float) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"image_id": str})
    required = {
        "reservoir", "image_date", "image_id", "boundary_version",
        "clear_area_fraction", "image_footprint_fraction", "scene_cloud_percent",
        *(column for column, variant in AREA_VARIANTS.items() if variant in variants.values()),
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Threshold-sweep CSV missing columns: {', '.join(sorted(missing))}")
    frame["date"] = pd.to_datetime(frame["image_date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].map(normalize_lake)
    for column in ("clear_area_fraction", "image_footprint_fraction", "scene_cloud_percent", *required.intersection(AREA_VARIANTS)):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["image_id"] = frame["image_id"].fillna("").astype(str).str.strip()
    frame = frame.dropna(subset=["date"])
    frame = frame[
        frame["reservoir"].isin(CAPACITY)
        & frame["boundary_version"].astype(str).str.strip().str.lower().eq("v3")
        & (frame["clear_area_fraction"] >= minimum_clear)
        & (frame["image_footprint_fraction"] >= minimum_footprint)
        & frame["image_id"].ne("")
    ].copy()
    frame["area_variant"] = frame["reservoir"].map(variants)
    column_by_variant = {variant: column for column, variant in AREA_VARIANTS.items()}
    frame["satellite_area_hectares"] = [
        row[column_by_variant[row["area_variant"]]] for _, row in frame.iterrows()
    ]
    frame["satellite_area_hectares"] = pd.to_numeric(frame["satellite_area_hectares"], errors="coerce")
    frame = frame.dropna(subset=["satellite_area_hectares"])
    frame = frame[frame["satellite_area_hectares"] > 0]
    return frame.drop_duplicates(["reservoir", "date", "image_id"], keep="first").reset_index(drop=True)


def load_exact_labels(
    scenes: pd.DataFrame,
    matches_path: Path,
    conflicts_path: Path,
) -> pd.DataFrame:
    matches = pd.read_csv(matches_path, dtype={"image_id": str})
    required = {
        "date", "reservoir", "image_id", "official_storage_mcft",
        "clear_area_fraction", "image_footprint_fraction", "boundary_version",
    }
    missing = required.difference(matches.columns)
    if missing:
        raise ValueError(f"Accuracy-ready CSV missing columns: {', '.join(sorted(missing))}")
    matches["date"] = pd.to_datetime(matches["date"], errors="coerce").dt.normalize()
    matches["reservoir"] = matches["reservoir"].map(normalize_lake)
    matches["image_id"] = matches["image_id"].fillna("").astype(str).str.strip()
    for column in ("official_storage_mcft", "clear_area_fraction", "image_footprint_fraction"):
        matches[column] = pd.to_numeric(matches[column], errors="coerce")
    matches = matches.dropna(subset=["date", "official_storage_mcft"])
    matches = matches[
        matches["reservoir"].isin(CAPACITY)
        & matches["boundary_version"].astype(str).str.strip().str.lower().eq("v3")
        & (matches["clear_area_fraction"] >= 0.95)
        & (matches["image_footprint_fraction"] >= 0.995)
        & matches["image_id"].ne("")
    ].copy()
    matches = matches.sort_values(
        ["reservoir", "date", "clear_area_fraction", "image_footprint_fraction", "image_id"],
        ascending=[True, True, False, False, True],
        kind="stable",
    ).drop_duplicates(["reservoir", "date"], keep="first")
    conflicts = conflict_keys(conflicts_path)
    matches["is_conflict"] = [
        (lake, pd.Timestamp(date)) in conflicts for lake, date in zip(matches["reservoir"], matches["date"])
    ]
    joined = scenes.merge(
        matches[["reservoir", "date", "image_id", "official_storage_mcft", "is_conflict"]],
        on=["reservoir", "date", "image_id"],
        how="left",
        validate="many_to_one",
    )
    joined["is_conflict"] = joined["is_conflict"].eq(True)
    joined["matched_exact_scene"] = joined["is_conflict"] | joined["official_storage_mcft"].notna()
    joined.loc[joined["is_conflict"], "official_storage_mcft"] = np.nan
    joined["official_storage_mcft"] = pd.to_numeric(joined["official_storage_mcft"], errors="coerce")
    joined.loc[
        joined.apply(
            lambda row: row["official_storage_mcft"] > CAPACITY[row["reservoir"]]
            or row["official_storage_mcft"] < 0,
            axis=1,
        ),
        "official_storage_mcft",
    ] = np.nan
    # Keep the exact matched scene on labeled dates so training and validation
    # use the same image ID. Otherwise prefer the cleanest available scene.
    return joined.sort_values(
        ["reservoir", "date", "matched_exact_scene", "clear_area_fraction", "image_footprint_fraction", "scene_cloud_percent", "image_id"],
        ascending=[True, True, False, False, False, True, True],
        kind="stable",
    ).drop_duplicates(["reservoir", "date"], keep="first").reset_index(drop=True)


def score(frame: pd.DataFrame) -> tuple[int, float, float, float, int, float]:
    valid = frame.dropna(subset=["satellite_estimate_mcft", "official_storage_mcft"])
    errors = valid["satellite_estimate_mcft"].to_numpy(dtype=float) - valid["official_storage_mcft"].to_numpy(dtype=float)
    low = valid["official_storage_mcft"].to_numpy(dtype=float) < 300.0
    return (
        len(errors),
        float(np.sqrt(np.mean(errors**2))) if len(errors) else float("nan"),
        float(np.mean(np.abs(errors))) if len(errors) else float("nan"),
        float(np.mean(errors)) if len(errors) else float("nan"),
        int(low.sum()),
        float(np.sqrt(np.mean(errors[low] ** 2))) if low.any() else float("nan"),
    )


def build_estimates(
    scenes: pd.DataFrame,
    minimum_pairs: int,
    minimum_unique_areas: int,
    cutoffs: dict[str, pd.Timestamp],
) -> pd.DataFrame:
    from sklearn.isotonic import IsotonicRegression

    rows: list[dict[str, object]] = []
    for lake, capacity in CAPACITY.items():
        group = scenes[scenes["reservoir"] == lake].sort_values("date").reset_index(drop=True)
        labels = group.dropna(subset=["official_storage_mcft"])
        for target in group.itertuples(index=False):
            prior = labels[labels["date"] < target.date]
            estimate: float | None = None
            if len(prior) >= minimum_pairs and prior["satellite_area_hectares"].nunique() >= minimum_unique_areas:
                model = IsotonicRegression(y_min=0.0, y_max=capacity, out_of_bounds="clip")
                model.fit(prior["satellite_area_hectares"].to_numpy(dtype=float), prior["official_storage_mcft"].to_numpy(dtype=float))
                estimate = float(model.predict([target.satellite_area_hectares])[0])
            rows.append({
                "date": pd.Timestamp(target.date).strftime("%Y-%m-%d"),
                "reservoir": lake,
                "satellite_area_hectares": float(target.satellite_area_hectares),
                "area_variant": target.area_variant,
                "validation_split": "holdout" if pd.Timestamp(target.date) >= cutoffs[lake] else "selection",
                "selection_cutoff_date": cutoffs[lake].strftime("%Y-%m-%d"),
                "satellite_estimate_mcft": estimate,
                "official_storage_mcft": target.official_storage_mcft,
                "estimate_error_mcft": (
                    estimate - float(target.official_storage_mcft)
                    if estimate is not None and pd.notna(target.official_storage_mcft)
                    else None
                ),
                "calibration_method": f"expanding isotonic; earlier exact-date matches only; {target.area_variant}",
                "calibration_pairs": int(len(prior)),
                "calibration_through_date": pd.Timestamp(prior["date"].max()).strftime("%Y-%m-%d") if len(prior) else "",
                "clear_area_fraction": float(target.clear_area_fraction),
                "image_footprint_fraction": float(target.image_footprint_fraction),
                "scene_cloud_percent": float(target.scene_cloud_percent),
                "boundary_version": target.boundary_version,
                "image_id": target.image_id,
            })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", default="aquasat_v3_sentinel2_threshold_sweep_2016_2026.csv")
    parser.add_argument("--matches", default="sentinel2_v3_clear95_accuracy_ready.csv")
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--selection-report", default="sentinel2_v3_clear95_threshold_comparison_candidate.csv")
    parser.add_argument("--output", default="sentinel2_v3_threshold_selected_oos_candidate.csv")
    parser.add_argument("--minimum-clear", type=float, default=0.95)
    parser.add_argument("--minimum-footprint", type=float, default=0.995)
    parser.add_argument("--minimum-training-pairs", type=int, default=8)
    parser.add_argument("--minimum-unique-areas", type=int, default=4)
    args = parser.parse_args()

    paths = [Path(args.candidates), Path(args.matches), Path(args.conflicts), Path(args.selection_report)]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise SystemExit("Missing input file(s): " + ", ".join(str(path.resolve()) for path in missing))
    variants = chosen_variants(Path(args.selection_report))
    cutoffs = selection_cutoffs(Path(args.selection_report))
    scenes = load_scenes(Path(args.candidates), variants, args.minimum_clear, args.minimum_footprint)
    scenes = load_exact_labels(scenes, Path(args.matches), Path(args.conflicts))
    output = build_estimates(scenes, args.minimum_training_pairs, args.minimum_unique_areas, cutoffs)

    print("Early-date-selected Sentinel-2 threshold candidates; prior-only isotonic calibration")
    print("Selected per-lake area variants:")
    for lake, variant in variants.items():
        print(f"  {lake}: {variant}")
    print(f"Quality-passing scenes: {len(output)}")
    print("Later 20% holdout scores only (early 80% selected the area rule; calibration remains prior-only):")
    for lake in CAPACITY:
        group = output[(output["reservoir"] == lake) & (output["validation_split"] == "holdout")]
        n, rmse, mae, bias, low_n, low_rmse = score(group)
        print(f"  {lake}: n={n}, RMSE={rmse:.1f} mcft, MAE={mae:.1f}, bias={bias:+.1f}; low-storage n={low_n}, RMSE={low_rmse:.1f}")

    output_path = Path(args.output)
    output.to_csv(output_path, index=False)
    print(f"Research candidate written: {output_path.resolve()}")


if __name__ == "__main__":
    main()
