"""Validate Sentinel-1 SAR water-area candidates and build an S2-first fallback.

The Earth Engine export is an experiment, not a production estimate. Candidate
VV/VH thresholds are selected on early exact-date CMWSSB matches within each
reservoir/orbit group. Storage calibration for every scene uses only earlier
official matches. Only acquisitions after the threshold-selection cutoff are
emitted for forecast features, preventing the selected threshold from leaking
back into the early selection period.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from build_sentinel2_storage import CAPACITY, conflict_keys, normalize_lake


MIN_PRIOR_PAIRS = 8
MIN_UNIQUE_AREAS = 4
MIN_SELECTION_PAIRS = 12
MIN_VALID_AREA_FRACTION = 0.95
MIN_FOOTPRINT_FRACTION = 0.995
SELECTION_FRACTION = 0.80


def rmse(errors: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(errors)))) if len(errors) else float("nan")


def parse_dates(frame: pd.DataFrame, candidates: tuple[str, ...]) -> pd.Series:
    name = next((column for column in candidates if column in frame.columns), None)
    if name is None:
        raise ValueError(f"Expected a date column from: {', '.join(candidates)}")
    if name == "image_ymd":
        return pd.to_datetime(frame[name].astype(str), format="%Y%m%d", errors="coerce").dt.normalize()

    # Ground-truth candidate files can contain legacy DD-MM-YYYY values and
    # newer ISO YYYY-MM-DD values in the same date_str column. Pandas' default
    # format inference can accept only the first style and silently coerce the
    # other rows to NaT. Use per-value parsing where supported, then explicit
    # common-format fallbacks for older pandas versions.
    values = frame[name].astype("string").str.strip()
    dayfirst = name == "date_str"
    try:
        parsed = pd.to_datetime(
            values, format="mixed", errors="coerce", dayfirst=dayfirst
        )
    except (TypeError, ValueError):
        parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")

    for date_format in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        missing = parsed.isna()
        if not missing.any():
            break
        parsed.loc[missing] = pd.to_datetime(
            values.loc[missing], format=date_format, errors="coerce"
        )

    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            values.loc[missing], errors="coerce", dayfirst=dayfirst
        )
    return pd.to_datetime(parsed, errors="coerce").dt.normalize()


def read_official(path: Path, excluded: set[tuple[str, str]]) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str)
    date_column = next((column for column in ("date", "date_str") if column in frame.columns), None)
    if date_column is None:
        raise ValueError("Ground-truth CSV needs a date or date_str column.")
    frame["date"] = parse_dates(frame, (date_column,))
    columns = [lake for lake in CAPACITY if lake in frame.columns]
    if not columns:
        raise ValueError("Ground-truth CSV needs wide columns for the reservoir names.")
    long = frame[["date", *columns]].melt(
        id_vars="date", var_name="reservoir", value_name="official_storage_mcft"
    )
    long["reservoir"] = long["reservoir"].map(normalize_lake)
    long["official_storage_mcft"] = pd.to_numeric(long["official_storage_mcft"], errors="coerce")
    long = long.dropna(subset=["date", "official_storage_mcft"])
    long = long[long["reservoir"].isin(CAPACITY)].copy()
    long["date_key"] = long["date"].dt.strftime("%Y-%m-%d")
    long = long[~long.apply(lambda row: (row.reservoir, row.date_key) in excluded, axis=1)]
    long = long[
        long.apply(
            lambda row: 0 <= row.official_storage_mcft <= CAPACITY[row.reservoir], axis=1
        )
    ]
    return long.sort_values(["reservoir", "date"]).drop_duplicates(
        ["reservoir", "date"], keep="last"
    )


def load_candidates(path: Path) -> tuple[pd.DataFrame, list[str]]:
    frame = pd.read_csv(path)
    frame["date"] = parse_dates(frame, ("image_date", "target_date", "date", "image_ymd"))
    required = {
        "reservoir", "boundary_version", "orbit_pass", "relative_orbit",
        "instrument_mode", "resolution_meters", "valid_area_fraction",
        "image_footprint_fraction",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Sentinel-1 candidate CSV is missing: {', '.join(sorted(missing))}")
    area_columns = [
        column for column in frame.columns
        if column.startswith(("vv_", "vh_")) and column.endswith("_area_hectares")
    ]
    if not area_columns:
        raise ValueError("No VV/VH threshold area columns were found in the S1 export.")
    frame["reservoir"] = frame["reservoir"].map(normalize_lake)
    frame["valid_area_fraction"] = pd.to_numeric(frame["valid_area_fraction"], errors="coerce")
    frame["image_footprint_fraction"] = pd.to_numeric(frame["image_footprint_fraction"], errors="coerce")
    for column in area_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame[
        (frame["boundary_version"].astype(str).str.strip().str.lower() == "v3") &
        (frame["instrument_mode"].astype(str).str.upper() == "IW") &
        (pd.to_numeric(frame["resolution_meters"], errors="coerce") == 10) &
        (frame["valid_area_fraction"] >= MIN_VALID_AREA_FRACTION) &
        (frame["image_footprint_fraction"] >= MIN_FOOTPRINT_FRACTION) &
        frame["reservoir"].isin(CAPACITY)
    ].copy()
    frame["orbit_pass"] = frame["orbit_pass"].fillna("UNKNOWN").astype(str).str.upper()
    frame["relative_orbit"] = frame["relative_orbit"].fillna("UNKNOWN").astype(str)
    frame["orbit_group"] = frame["orbit_pass"] + "_R" + frame["relative_orbit"]
    if "image_id" not in frame:
        frame["image_id"] = ""
    frame["image_id"] = frame["image_id"].fillna("").astype(str)
    frame = frame.dropna(subset=["date"])
    # Same-date granules within the same orbit are represented by the highest-
    # coverage scene. This prevents duplicate scenes from inflating scores.
    frame = frame.sort_values(
        ["reservoir", "orbit_group", "date", "valid_area_fraction", "image_footprint_fraction", "image_id"],
        ascending=[True, True, True, False, False, True],
        kind="stable",
    ).drop_duplicates(["reservoir", "orbit_group", "date"], keep="first")
    return frame.reset_index(drop=True), area_columns


def expanding_predictions(
    rows: pd.DataFrame,
    official: pd.DataFrame,
    area_column: str,
    capacity: float,
) -> dict[pd.Timestamp, float]:
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError as exc:
        raise SystemExit("scikit-learn is required. Install it with: python -m pip install scikit-learn") from exc
    label_by_date = official.set_index("date")["official_storage_mcft"]
    dates: list[pd.Timestamp] = []
    values: list[float] = []
    for row in rows.sort_values("date").itertuples(index=False):
        area = getattr(row, area_column)
        if pd.isna(area):
            continue
        prior_dates = label_by_date.index[label_by_date.index < row.date]
        if len(prior_dates) < MIN_PRIOR_PAIRS:
            continue
        prior = rows[
            rows["date"].isin(prior_dates)
        ][["date", area_column]].dropna().drop_duplicates("date", keep="last")
        prior = prior.sort_values("date")
        if len(prior) < MIN_PRIOR_PAIRS or prior[area_column].nunique() < MIN_UNIQUE_AREAS:
            continue
        targets = prior["date"].map(label_by_date)
        model = IsotonicRegression(y_min=0.0, y_max=capacity, out_of_bounds="clip")
        model.fit(prior[area_column].to_numpy(dtype=float), targets.to_numpy(dtype=float))
        dates.append(pd.Timestamp(row.date).normalize())
        values.append(float(model.predict([float(area)])[0]))
    return dict(zip(dates, values))


def evaluate_group(
    rows: pd.DataFrame,
    labels: pd.DataFrame,
    lake: str,
    orbit_group: str,
    area_columns: list[str],
) -> tuple[dict[str, object] | None, list[dict[str, object]], pd.Timestamp | None]:
    group = rows[(rows["reservoir"] == lake) & (rows["orbit_group"] == orbit_group)].copy()
    official = labels[labels["reservoir"] == lake][["date", "official_storage_mcft"]].copy()
    match_dates = sorted(set(group["date"]) & set(official["date"]))
    if len(match_dates) < MIN_SELECTION_PAIRS + 5:
        return None, [], None
    selection_count = max(MIN_SELECTION_PAIRS, int(np.floor(len(match_dates) * SELECTION_FRACTION)))
    selection_count = min(selection_count, len(match_dates) - 5)
    cutoff = pd.Timestamp(match_dates[selection_count - 1]).normalize()
    predictions_by_variant: dict[str, dict[pd.Timestamp, float]] = {}
    for area_column in area_columns:
        predictions_by_variant[area_column] = expanding_predictions(
            group, official, area_column, CAPACITY[lake]
        )

    selection_dates = set(match_dates[:selection_count])
    eligible_variants = [
        name for name in area_columns
        if len(selection_dates & set(predictions_by_variant[name])) >= MIN_SELECTION_PAIRS
    ]
    if not eligible_variants:
        return None, [], cutoff
    common_dates = set.intersection(*[
        set(predictions_by_variant[name]) for name in eligible_variants
    ])
    common_selection = sorted(selection_dates & common_dates)
    if len(common_selection) < MIN_SELECTION_PAIRS:
        return None, [], cutoff
    label_map = official.set_index("date")["official_storage_mcft"].to_dict()
    scores: list[dict[str, object]] = []
    for area_column in eligible_variants:
        errors = np.array([
            predictions_by_variant[area_column][date] - float(label_map[date])
            for date in common_selection
        ], dtype=float)
        scores.append({
            "reservoir": lake,
            "orbit_group": orbit_group,
            "variant": area_column,
            "selection_pairs": len(errors),
            "selection_rmse_mcft": rmse(errors),
            "selection_mae_mcft": float(np.mean(np.abs(errors))),
            "selection_bias_mcft": float(np.mean(errors)),
        })
    selected = min(scores, key=lambda item: (float(item["selection_rmse_mcft"]), str(item["variant"])))
    chosen = str(selected["variant"])

    holdout_dates = [date for date in match_dates if date > cutoff and date in predictions_by_variant[chosen]]
    holdout_errors = np.array([
        predictions_by_variant[chosen][date] - float(label_map[date])
        for date in holdout_dates
    ], dtype=float)
    selected["selection_cutoff_date"] = cutoff.strftime("%Y-%m-%d")
    selected["holdout_pairs"] = len(holdout_errors)
    selected["holdout_rmse_mcft"] = rmse(holdout_errors)
    selected["holdout_mae_mcft"] = (
        float(np.mean(np.abs(holdout_errors))) if len(holdout_errors) else float("nan")
    )
    selected["holdout_bias_mcft"] = (
        float(np.mean(holdout_errors)) if len(holdout_errors) else float("nan")
    )

    output: list[dict[str, object]] = []
    label_map_by_date = official.set_index("date")["official_storage_mcft"].to_dict()
    for row in group[group["date"] > cutoff].itertuples(index=False):
        date = pd.Timestamp(row.date).normalize()
        estimate = predictions_by_variant[chosen].get(date)
        if estimate is None:
            continue
        actual = label_map_by_date.get(date)
        prior_pair_dates = sorted(set(group.loc[group["date"] < date, "date"]) & set(official["date"]))
        output.append({
            "date": date.strftime("%Y-%m-%d"),
            "reservoir": lake,
            "satellite_estimate_mcft": estimate,
            "source": "sentinel1_fallback_candidate",
            "sensor": "sentinel-1 sar",
            "selected_area_variant": chosen,
            "calibration_method": "expanding isotonic; earlier exact-date CMWSSB matches only",
            "calibration_through_date": (
                max(prior_pair_dates).strftime("%Y-%m-%d") if prior_pair_dates else ""
            ),
            "selection_cutoff_date": cutoff.strftime("%Y-%m-%d"),
            "calibration_pairs": len(prior_pair_dates),
            "official_storage_mcft": actual,
            "estimate_error_mcft": estimate - actual if actual is not None else np.nan,
            "orbit_pass": row.orbit_pass,
            "relative_orbit": row.relative_orbit,
            "image_id": row.image_id,
            "valid_area_fraction": float(row.valid_area_fraction),
            "image_footprint_fraction": float(row.image_footprint_fraction),
            "boundary_version": "v3",
            "quality_pass": True,
            "status": "estimated_after_early_threshold_selection",
        })
    return selected, output, cutoff


def read_s2_estimates(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"Sentinel-2 OOS file not found: {path.resolve()}")
    frame = pd.read_csv(path)
    required = {"date", "reservoir", "satellite_estimate_mcft"}
    if not required.issubset(frame.columns):
        raise ValueError("Sentinel-2 OOS file needs date, reservoir, and satellite_estimate_mcft.")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].map(normalize_lake)
    frame["satellite_estimate_mcft"] = pd.to_numeric(frame["satellite_estimate_mcft"], errors="coerce")
    frame = frame.dropna(subset=["date", "satellite_estimate_mcft"])
    if "quality_pass" in frame:
        frame = frame[frame["quality_pass"].astype(str).str.lower().isin({"true", "1", "yes"})]
    if "boundary_version" in frame:
        frame = frame[frame["boundary_version"].astype(str).str.strip().str.lower() == "v3"]
    frame["source"] = "sentinel2"
    frame["sensor"] = "sentinel-2 optical"
    frame["quality_pass"] = True
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    return frame[["date", "reservoir", "satellite_estimate_mcft", "source", "sensor", "quality_pass"]]


def build_outputs(
    candidates_path: Path,
    ground_truth_path: Path,
    conflicts_path: Path,
    sentinel2_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, int]]:
    excluded = conflict_keys(conflicts_path)
    labels = read_official(ground_truth_path, excluded)
    candidates, area_columns = load_candidates(candidates_path)
    label_keys = labels[["reservoir", "date", "official_storage_mcft"]]
    candidates = candidates.merge(label_keys, on=["reservoir", "date"], how="left")

    selected_rows: list[dict[str, object]] = []
    fallback_rows: list[dict[str, object]] = []
    for lake in CAPACITY:
        lake_groups = sorted(candidates.loc[candidates["reservoir"] == lake, "orbit_group"].unique())
        for orbit_group in lake_groups:
            selected, output, _ = evaluate_group(
                candidates, labels, lake, orbit_group, area_columns
            )
            if selected is not None:
                selected_rows.append(selected)
                fallback_rows.extend(output)

    fallback = pd.DataFrame(fallback_rows)
    if fallback.empty:
        fallback = pd.DataFrame(columns=[
            "date", "reservoir", "satellite_estimate_mcft", "source", "sensor",
            "selected_area_variant", "selection_cutoff_date", "calibration_through_date",
            "official_storage_mcft", "estimate_error_mcft", "orbit_pass", "relative_orbit",
            "image_id", "valid_area_fraction", "image_footprint_fraction", "boundary_version",
            "quality_pass", "status",
        ])
    else:
        # If more than one orbit scene survives on a lake/date, keep the
        # candidate with strongest valid-area and footprint coverage.
        fallback = fallback.sort_values(
            ["reservoir", "date", "valid_area_fraction", "image_footprint_fraction", "image_id"],
            ascending=[True, True, False, False, True],
            kind="stable",
        ).drop_duplicates(["reservoir", "date"], keep="first")

    s2 = read_s2_estimates(sentinel2_path)
    fallback_for_merge = fallback[[
        "date", "reservoir", "satellite_estimate_mcft", "source", "sensor", "quality_pass"
    ]].copy()
    s2_keys = set(zip(s2["reservoir"], s2["date"]))
    fallback_for_merge = fallback_for_merge[
        ~fallback_for_merge.apply(lambda row: (row.reservoir, row.date) in s2_keys, axis=1)
    ]
    combined = pd.concat([s2, fallback_for_merge], ignore_index=True)
    combined = combined.sort_values(["reservoir", "date", "source"], kind="stable")
    selected_frame = pd.DataFrame(selected_rows)
    return fallback.reset_index(drop=True), combined.reset_index(drop=True), selected_frame, {
        "candidate_rows_after_quality": len(candidates),
        "official_label_pairs": len(labels),
        "exact_date_match_pairs": int(candidates["official_storage_mcft"].notna().sum()),
        "candidate_variant_columns": len(area_columns),
        "excluded_conflict_pairs": len(excluded),
        "sentinel2_rows": len(s2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True, help="Downloaded v3 dual-pol S1 candidate CSV.")
    parser.add_argument("--ground-truth", default="reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv")
    parser.add_argument("--conflicts", default="ground_truth_conflicts_for_review.csv")
    parser.add_argument("--sentinel2", default="sentinel2_v3_clear95_oos_storage_candidate.csv")
    parser.add_argument("--output", default="sentinel1_v3_oos_fallback_candidate.csv")
    parser.add_argument("--combined-output", default="sentinel2_sentinel1_oos_storage_candidate.csv")
    args = parser.parse_args()
    for path in (Path(args.candidates), Path(args.ground_truth), Path(args.conflicts), Path(args.sentinel2)):
        if not path.exists():
            raise SystemExit(f"Required input file not found: {path.resolve()}")

    fallback, combined, selections, counts = build_outputs(
        Path(args.candidates), Path(args.ground_truth), Path(args.conflicts), Path(args.sentinel2)
    )
    fallback.to_csv(args.output, index=False)
    combined.to_csv(args.combined_output, index=False)
    print("Sentinel-1 v3 fallback candidate validation")
    print(
        f"Kept {counts['candidate_rows_after_quality']} S1 rows after v3/IW/10m, "
        f">={MIN_VALID_AREA_FRACTION:.0%} valid-area and >={MIN_FOOTPRINT_FRACTION:.1%} footprint gates."
    )
    print(
        f"Loaded {counts['official_label_pairs']} non-disputed official label pairs; "
        f"{counts['exact_date_match_pairs']} quality-passing S1 scenes have exact-date labels."
    )
    print(f"Threshold variants tested: {counts['candidate_variant_columns']}; disputed official pairs excluded: {counts['excluded_conflict_pairs']}.")
    if selections.empty:
        print("No orbit groups had enough early exact-date matches for honest selection and later holdout scoring.")
    else:
        print("Selected thresholds were chosen only on early dates; later rows are the holdout check.")
        print(selections.to_string(index=False, formatters={
            "selection_rmse_mcft": lambda value: "—" if pd.isna(value) else f"{value:.1f}",
            "selection_mae_mcft": lambda value: "—" if pd.isna(value) else f"{value:.1f}",
            "selection_bias_mcft": lambda value: "—" if pd.isna(value) else f"{value:+.1f}",
            "holdout_rmse_mcft": lambda value: "—" if pd.isna(value) else f"{value:.1f}",
            "holdout_mae_mcft": lambda value: "—" if pd.isna(value) else f"{value:.1f}",
            "holdout_bias_mcft": lambda value: "—" if pd.isna(value) else f"{value:+.1f}",
        }))
    print(f"S1 fallback rows written: {len(fallback)} -> {Path(args.output).resolve()}")
    print(f"Combined S2-first / S1-fallback rows written: {len(combined)} -> {Path(args.combined_output).resolve()}")
    print("These are research candidates. Compare the forecast backtest before connecting any SAR estimate to the live dashboard.")


if __name__ == "__main__":
    main()
