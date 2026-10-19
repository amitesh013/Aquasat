"""Create leakage-safe storage estimates from quality-filtered Sentinel-2 scenes.

The script calibrates each scene using only earlier exact-date Sentinel-2 /
CMWSSB matches. It writes a separate candidate file and does not replace the
reviewed data, dashboard data, or source satellite records.
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


def parse_dates(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    column = next((name for name in columns if name in frame.columns), None)
    if column is None:
        raise ValueError(f"Expected one date column from: {', '.join(columns)}")
    values = frame[column]
    if column == "image_ymd":
        return pd.to_datetime(values.astype(str), format="%Y%m%d", errors="coerce").dt.normalize()
    return pd.to_datetime(values, errors="coerce").dt.normalize()


def conflict_keys(path: Path) -> set[tuple[str, str]]:
    frame = pd.read_csv(path, dtype=str)
    if not {"date", "reservoir"}.issubset(frame.columns):
        raise ValueError("Conflict CSV must contain date and reservoir columns.")
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    lakes = frame["reservoir"].map(normalize_lake)
    return {
        (lake, date.strftime("%Y-%m-%d"))
        for lake, date in zip(lakes, dates)
        if lake and pd.notna(date)
    }


def add_quality_fields(
    frame: pd.DataFrame,
    label: str,
    minimum_clear: float,
    minimum_footprint: float,
) -> pd.DataFrame:
    required = {"reservoir", "clear_area_fraction", "image_footprint_fraction"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{label} CSV is missing required quality columns: {', '.join(sorted(missing))}")
    frame = frame.copy()
    frame["reservoir"] = frame["reservoir"].map(normalize_lake)
    frame["clear_area_fraction"] = pd.to_numeric(frame["clear_area_fraction"], errors="coerce")
    frame["image_footprint_fraction"] = pd.to_numeric(frame["image_footprint_fraction"], errors="coerce")
    if "scene_cloud_percent" not in frame:
        frame["scene_cloud_percent"] = 0.0
    frame["scene_cloud_percent"] = pd.to_numeric(frame["scene_cloud_percent"], errors="coerce").fillna(100.0)
    if "boundary_version" not in frame:
        raise ValueError(f"{label} CSV must include boundary_version so v3 measurements can be verified.")
    frame = frame[frame["boundary_version"].astype(str).str.strip().str.lower() == "v3"]
    frame = frame[
        frame["reservoir"].isin(CAPACITY) &
        (frame["clear_area_fraction"] >= minimum_clear) &
        (frame["image_footprint_fraction"] >= minimum_footprint)
    ].copy()
    return frame


def rank_and_deduplicate(frame: pd.DataFrame) -> pd.DataFrame:
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


def rmse(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values)))) if len(values) else float("nan")


def build_estimates(
    candidates_path: Path,
    matches_path: Path,
    conflicts_path: Path,
    minimum_clear: float,
    minimum_footprint: float,
    minimum_pairs: int,
    minimum_unique_areas: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError as exc:
        raise SystemExit("scikit-learn is required. Install it with: python -m pip install scikit-learn") from exc

    conflict_set = conflict_keys(conflicts_path)

    candidates = pd.read_csv(candidates_path)
    candidates["date"] = parse_dates(candidates, ("image_date", "target_date", "date", "image_ymd"))
    candidates = add_quality_fields(candidates, "Candidate", minimum_clear, minimum_footprint)
    if "water_area_hectares" not in candidates:
        raise ValueError("Candidate CSV must contain water_area_hectares.")
    candidates["satellite_area_hectares"] = pd.to_numeric(candidates["water_area_hectares"], errors="coerce")
    candidates = candidates.dropna(subset=["date", "satellite_area_hectares"])
    candidates = candidates[candidates["satellite_area_hectares"] > 0]
    candidates = candidates[
        (candidates["clear_area_fraction"] >= minimum_clear) &
        (candidates["image_footprint_fraction"] >= minimum_footprint)
    ]
    candidates = rank_and_deduplicate(candidates)

    matches = pd.read_csv(matches_path)
    required_matches = {"date", "reservoir", "official_storage_mcft", "satellite_area_hectares"}
    missing = required_matches.difference(matches.columns)
    if missing:
        raise ValueError(f"Matched accuracy CSV is missing columns: {', '.join(sorted(missing))}")
    matches["date"] = parse_dates(matches, ("date",))
    matches = add_quality_fields(matches, "Matched accuracy", minimum_clear, minimum_footprint)
    matches["official_storage_mcft"] = pd.to_numeric(matches["official_storage_mcft"], errors="coerce")
    matches["satellite_area_hectares"] = pd.to_numeric(matches["satellite_area_hectares"], errors="coerce")
    matches = matches.dropna(subset=["date", "official_storage_mcft", "satellite_area_hectares"])
    matches = matches[
        (matches["clear_area_fraction"] >= minimum_clear) &
        (matches["image_footprint_fraction"] >= minimum_footprint) &
        (matches["satellite_area_hectares"] > 0)
    ].copy()
    matches = matches[
        matches.apply(
            lambda row: 0.0 <= row["official_storage_mcft"] <= CAPACITY[row["reservoir"]],
            axis=1,
        )
    ]
    matches = rank_and_deduplicate(matches)

    clean_matches = matches[
        ~matches.apply(lambda row: (row["reservoir"], row["date"].strftime("%Y-%m-%d")) in conflict_set, axis=1)
    ].copy()
    actual_by_key = {
        (row.reservoir, pd.Timestamp(row.date).strftime("%Y-%m-%d")): float(row.official_storage_mcft)
        for row in clean_matches.itertuples(index=False)
    }

    estimates: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    for lake, capacity in CAPACITY.items():
        lake_candidates = candidates[candidates["reservoir"] == lake].sort_values("date")
        lake_matches = clean_matches[clean_matches["reservoir"] == lake].sort_values("date")
        counts[lake] = len(lake_candidates)
        for row in lake_candidates.itertuples(index=False):
            prior = lake_matches[lake_matches["date"] < row.date]
            estimate: float | None = None
            status = "insufficient_prior_matches"
            if len(prior) >= minimum_pairs and prior["satellite_area_hectares"].nunique() >= minimum_unique_areas:
                model = IsotonicRegression(y_min=0.0, y_max=capacity, out_of_bounds="clip")
                model.fit(prior["satellite_area_hectares"], prior["official_storage_mcft"])
                estimate = float(model.predict([row.satellite_area_hectares])[0])
                status = "estimated_from_prior_matches"
            key = (lake, pd.Timestamp(row.date).strftime("%Y-%m-%d"))
            actual = actual_by_key.get(key)
            # Conflicted official labels are never emitted as validation targets.
            is_conflict = key in conflict_set
            if is_conflict:
                actual = None
            train_through = pd.Timestamp(prior["date"].max()).strftime("%Y-%m-%d") if not prior.empty else ""
            estimates.append({
                "date": pd.Timestamp(row.date).strftime("%Y-%m-%d"),
                "reservoir": lake,
                "satellite_area_hectares": float(row.satellite_area_hectares),
                "satellite_estimate_mcft": estimate,
                "official_storage_mcft": actual,
                "estimate_error_mcft": estimate - actual if estimate is not None and actual is not None else None,
                "calibration_method": "expanding isotonic; earlier exact-date matches only",
                "calibration_pairs": int(len(prior)),
                "calibration_through_date": train_through,
                "clear_area_fraction": float(row.clear_area_fraction),
                "image_footprint_fraction": float(row.image_footprint_fraction),
                "scene_cloud_percent": float(row.scene_cloud_percent),
                "boundary_version": getattr(row, "boundary_version", "v3"),
                "image_id": getattr(row, "image_id", ""),
                "quality_pass": True,
                "status": status,
            })

    output = pd.DataFrame(estimates)
    summary_rows: list[dict[str, object]] = []
    for lake, capacity in CAPACITY.items():
        group = output[
            (output["reservoir"] == lake) &
            output["satellite_estimate_mcft"].notna() &
            output["official_storage_mcft"].notna()
        ]
        error = pd.to_numeric(group["estimate_error_mcft"], errors="coerce").dropna().to_numpy(dtype=float)
        summary_rows.append({
            "reservoir": lake,
            "pairs_scored": len(error),
            "rmse_mcft": rmse(error),
            "rmse_pct_capacity": rmse(error) / capacity * 100 if len(error) else float("nan"),
            "mae_mcft": float(np.mean(np.abs(error))) if len(error) else float("nan"),
            "bias_mcft": float(np.mean(error)) if len(error) else float("nan"),
        })
    return output, pd.DataFrame(summary_rows), counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, help="Full v3 clear95 Sentinel-2 acquisition CSV.")
    parser.add_argument("--matches", default="sentinel2_v3_clear95_accuracy_ready.csv")
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--output", default="sentinel2_v3_clear95_oos_storage_candidate.csv")
    parser.add_argument("--minimum-clear", type=float, default=0.95)
    parser.add_argument("--minimum-footprint", type=float, default=0.995)
    parser.add_argument("--minimum-training-pairs", type=int, default=8)
    parser.add_argument("--minimum-unique-areas", type=int, default=4)
    args = parser.parse_args()

    for path in (Path(args.candidates), Path(args.matches), Path(args.conflicts)):
        if not path.exists():
            raise SystemExit(f"Required input file not found: {path.resolve()}")

    output, summary, candidate_counts = build_estimates(
        Path(args.candidates),
        Path(args.matches),
        Path(args.conflicts),
        args.minimum_clear,
        args.minimum_footprint,
        args.minimum_training_pairs,
        args.minimum_unique_areas,
    )
    output_path = Path(args.output)
    output.to_csv(output_path, index=False)

    print("Sentinel-2 → storage calibration (chronological out-of-sample estimates)")
    print(f"Quality gate: clear area >= {args.minimum_clear:.0%}; image footprint >= {args.minimum_footprint:.1%}; v3 boundary")
    print("Disputed official lake/date values were excluded from calibration and scoring.")
    print("Satellite estimates are created for every quality-passing image after enough earlier matches exist.")
    print("\nAcquisitions retained and validation scores by lake:")
    display = summary.copy()
    display.insert(1, "images", [candidate_counts[lake] for lake in summary["reservoir"]])
    print(display.to_string(index=False, formatters={
        "rmse_mcft": lambda value: "—" if pd.isna(value) else f"{value:.1f}",
        "rmse_pct_capacity": lambda value: "—" if pd.isna(value) else f"{value:.1f}%",
        "mae_mcft": lambda value: "—" if pd.isna(value) else f"{value:.1f}",
        "bias_mcft": lambda value: "—" if pd.isna(value) else f"{value:+.1f}",
    }))
    print(f"\nEstimate rows written: {len(output)}")
    print(f"Candidate output: {output_path.resolve()}")


if __name__ == "__main__":
    main()
