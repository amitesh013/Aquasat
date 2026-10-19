"""Leakage-safe XGBoost forecast features for AquaSat reservoir storage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


MODEL_CONFIGS = (
    {
        "name": "shallow_recent",
        "n_estimators": 180,
        "max_depth": 1,
        "learning_rate": 0.035,
        "min_child_weight": 8,
        "reg_lambda": 12.0,
        "reg_alpha": 0.5,
        "half_life_years": 4.0,
    },
    {
        "name": "medium_recent",
        "n_estimators": 240,
        "max_depth": 2,
        "learning_rate": 0.025,
        "min_child_weight": 8,
        "reg_lambda": 15.0,
        "reg_alpha": 0.75,
        "half_life_years": 4.0,
    },
    {
        "name": "medium_longer_history",
        "n_estimators": 300,
        "max_depth": 2,
        "learning_rate": 0.02,
        "min_child_weight": 10,
        "reg_lambda": 20.0,
        "reg_alpha": 1.0,
        "half_life_years": 8.0,
    },
    {
        "name": "legacy_regularized",
        "n_estimators": 120,
        "max_depth": 2,
        "learning_rate": 0.04,
        "min_child_weight": 5,
        "reg_lambda": 5.0,
        "reg_alpha": 0.0,
        "half_life_years": 100.0,
    },
)


def read_satellite_histories(
    path: Path,
    capacities: dict[str, float],
    excluded_pairs: set[tuple[str, str]] | None = None,
) -> dict[str, pd.Series]:
    """Load leakage-safe satellite storage estimates for forecast features.

    A prebuilt OOS candidate file can contain estimates for all image dates.
    It may contain Sentinel-2 estimates and/or a later Sentinel-1 fallback;
    both use the same date/reservoir/estimate schema and are only lag features.
    The legacy paired-area input is also supported; its estimates are rebuilt
    from Sentinel-2 exact-date matches with expanding calibration and disputed
    official pairs excluded. XGBoost does not ingest raw MNDWI or SAR pixels.
    """
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    if not {"date", "reservoir"}.issubset(frame.columns):
        raise ValueError("Satellite CSV must contain date and reservoir columns.")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].astype(str).str.strip().str.lower()

    # The dedicated Sentinel-2 pipeline exports a point-in-time calibrated
    # estimate for every qualifying image, including unmatched image dates.
    if {"date", "reservoir", "satellite_estimate_mcft"}.issubset(frame.columns):
        frame["satellite_estimate_mcft"] = pd.to_numeric(frame["satellite_estimate_mcft"], errors="coerce")
        frame = frame.dropna(subset=["date", "satellite_estimate_mcft"])
        if "quality_pass" in frame:
            frame = frame[frame["quality_pass"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})]
        output: dict[str, pd.Series] = {}
        for lake in capacities:
            group = frame[frame["reservoir"] == lake].sort_values("date").drop_duplicates("date", keep="last")
            if not group.empty:
                output[lake] = pd.Series(
                    group["satellite_estimate_mcft"].to_numpy(dtype=float),
                    index=pd.DatetimeIndex(group["date"]),
                    dtype=float,
                ).sort_index()
        return output

    from sklearn.isotonic import IsotonicRegression

    required = {"date", "reservoir", "official_storage_mcft", "satellite_area_hectares"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Satellite CSV is missing columns: {', '.join(sorted(missing))}")
    frame["official_storage_mcft"] = pd.to_numeric(frame["official_storage_mcft"], errors="coerce")
    frame["satellite_area_hectares"] = pd.to_numeric(frame["satellite_area_hectares"], errors="coerce")
    frame = frame.dropna(subset=["date", "official_storage_mcft", "satellite_area_hectares"])
    if "clear_area_fraction" not in frame or "image_footprint_fraction" not in frame:
        raise ValueError(
            "Raw Sentinel-2 matches need clear_area_fraction and image_footprint_fraction. "
            "Build the quality-filtered OOS file with build_sentinel2_storage.py first."
        )
    frame["clear_area_fraction"] = pd.to_numeric(frame["clear_area_fraction"], errors="coerce")
    frame["image_footprint_fraction"] = pd.to_numeric(frame["image_footprint_fraction"], errors="coerce")
    if "boundary_version" not in frame:
        raise ValueError("Raw Sentinel-2 matches must identify boundary_version; v3 is required.")
    frame = frame[
        (frame["clear_area_fraction"] >= 0.95) &
        (frame["image_footprint_fraction"] >= 0.995) &
        (frame["boundary_version"].astype(str).str.strip().str.lower() == "v3")
    ]
    if excluded_pairs:
        keys = list(zip(frame["reservoir"], frame["date"].dt.strftime("%Y-%m-%d")))
        frame = frame.loc[[key not in excluded_pairs for key in keys]]
    output: dict[str, pd.Series] = {}

    for lake, capacity in capacities.items():
        group = frame[frame["reservoir"] == lake].copy()
        if "scene_cloud_percent" not in group:
            group["scene_cloud_percent"] = 0.0
        else:
            group["scene_cloud_percent"] = pd.to_numeric(group["scene_cloud_percent"], errors="coerce").fillna(100.0)
        group = group.sort_values(
            ["date", "clear_area_fraction", "image_footprint_fraction", "scene_cloud_percent"],
            ascending=[True, False, False, True],
            kind="stable",
        ).drop_duplicates("date", keep="first")
        estimates: list[float] = []
        dates: list[pd.Timestamp] = []
        for index, row in enumerate(group.itertuples(index=False)):
            prior = group.iloc[:index]
            estimate = float("nan")
            if len(prior) >= 8 and prior["satellite_area_hectares"].nunique() >= 4:
                model = IsotonicRegression(y_min=0.0, y_max=capacity, out_of_bounds="clip")
                model.fit(prior["satellite_area_hectares"], prior["official_storage_mcft"])
                estimate = float(model.predict([row.satellite_area_hectares])[0])
            dates.append(pd.Timestamp(row.date).normalize())
            estimates.append(estimate)
        if dates:
            output[lake] = pd.Series(estimates, index=pd.DatetimeIndex(dates), dtype=float).dropna().sort_index()
    return output


def xgb_features(
    history: pd.Series,
    origin: pd.Timestamp,
    target: pd.Timestamp,
    capacity: float,
    satellite_history: pd.Series | None = None,
    rainfall_history: pd.Series | None = None,
) -> dict[str, float]:
    """Make features using observations available on or before the origin.

    Optional rainfall features are antecedent accumulations only. They exclude
    the forecast-origin date as well as all later rainfall.
    """
    past = history[history.index <= origin].dropna().sort_index()
    if past.empty:
        raise ValueError("Cannot build forecast features without an observed storage value.")
    last = float(past.iloc[-1])
    features: dict[str, float] = {
        "storage_fraction": last / capacity,
        "lead_days": float((target - origin).days),
        "days_since_last_observation": float((origin - past.index[-1]).days),
    }

    for days in (7, 14, 30, 60, 90, 180, 365):
        cutoff = origin - pd.Timedelta(days=days)
        earlier = past[past.index <= cutoff]
        reference = float(earlier.iloc[-1]) if not earlier.empty else last
        recent = past[past.index > cutoff]
        if recent.empty:
            recent = past.iloc[[-1]]
        features[f"lag_{days}d_fraction"] = reference / capacity
        features[f"change_{days}d_fraction"] = (last - reference) / capacity
        features[f"mean_{days}d_fraction"] = float(recent.mean()) / capacity
        features[f"std_{days}d_fraction"] = float(recent.std(ddof=0)) / capacity
        features[f"count_{days}d"] = float(len(recent))

    target_month_angle = 2.0 * np.pi * target.month / 12.0
    target_year_angle = 2.0 * np.pi * target.dayofyear / (366.0 if target.is_leap_year else 365.0)
    features["target_month_sin"] = float(np.sin(target_month_angle))
    features["target_month_cos"] = float(np.cos(target_month_angle))
    features["target_year_sin"] = float(np.sin(target_year_angle))
    features["target_year_cos"] = float(np.cos(target_year_angle))

    if rainfall_history is not None and not rainfall_history.empty:
        rain = rainfall_history.copy()
        rain.index = pd.to_datetime(rain.index).normalize()
        rain = pd.to_numeric(rain, errors="coerce").dropna().sort_index()
        rain = rain[~rain.index.duplicated(keep="last")]
        rain = rain[rain.index < origin]
        for days in (7, 30, 90):
            start = origin - pd.Timedelta(days, unit="D")
            window = rain[(rain.index >= start) & (rain.index < origin)]
            # Do not silently treat missing rainfall dates as zero rainfall.
            complete = len(window) == days and window.index.nunique() == days
            features[f"rainfall_{days}d_mm"] = (
                float(window.sum()) if complete else float("nan")
            )

    if satellite_history is not None and not satellite_history.empty:
        available_satellite = satellite_history[satellite_history.index <= origin].dropna().sort_index()
    else:
        available_satellite = pd.Series(dtype=float)
    if available_satellite.empty:
        features.update({
            "satellite_available": 0.0,
            "satellite_storage_fraction": float("nan"),
            "satellite_minus_official_fraction": float("nan"),
            "satellite_age_days": 9999.0,
        })
    else:
        satellite_date = available_satellite.index[-1]
        satellite_estimate = float(available_satellite.iloc[-1])
        features.update({
            "satellite_available": 1.0,
            "satellite_storage_fraction": satellite_estimate / capacity,
            "satellite_minus_official_fraction": (satellite_estimate - last) / capacity,
            "satellite_age_days": float((origin - satellite_date).days),
        })
    return features


def _make_estimator(config: dict[str, float | int | str]):
    try:
        from xgboost import XGBRegressor
    except ImportError as exc:
        raise SystemExit("XGBoost is required. Install it with: python -m pip install xgboost") from exc
    return XGBRegressor(
        objective="reg:squarederror",
        n_estimators=int(config["n_estimators"]),
        max_depth=int(config["max_depth"]),
        learning_rate=float(config["learning_rate"]),
        min_child_weight=int(config["min_child_weight"]),
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=float(config["reg_lambda"]),
        reg_alpha=float(config["reg_alpha"]),
        tree_method="hist",
        n_jobs=1,
        random_state=42,
        verbosity=0,
    )


def _sample_weight(anchors: pd.Series, origin: pd.Timestamp, half_life_years: float) -> np.ndarray:
    age_years = (origin - pd.to_datetime(anchors)).dt.days.to_numpy(dtype=float) / 365.25
    return np.power(0.5, np.maximum(age_years, 0.0) / half_life_years)


def build_xgb_training_pool(
    history: pd.Series,
    horizon: int,
    tolerance: int,
    capacity: float,
    satellite_history: pd.Series | None,
    rainfall_history: pd.Series | None = None,
) -> pd.DataFrame:
    """Precompute origin-safe features once for all historical weekly anchors."""
    known = history.dropna().sort_index()
    if len(known) < 35:
        return pd.DataFrame()
    # About one training example per week limits overlap between 15-day labels.
    anchors = known.index[::7]
    examples: list[dict[str, float | pd.Timestamp]] = []
    for anchor in anchors:
        candidates = known[
            (known.index >= anchor + pd.Timedelta(days=max(1, horizon - tolerance))) &
            (known.index <= anchor + pd.Timedelta(days=horizon + tolerance))
        ]
        if candidates.empty:
            continue
        anchor_history = known[known.index <= anchor]
        if anchor_history.empty:
            continue
        current = float(anchor_history.iloc[-1])
        typical_target = anchor + pd.Timedelta(days=horizon)
        features = xgb_features(
            known, anchor, typical_target, capacity, satellite_history, rainfall_history
        )
        features["anchor_date"] = anchor
        features["anchor_storage_mcft"] = current
        features["candidate_dates"] = tuple(candidates.index)
        features["candidate_values"] = tuple(float(value) for value in candidates.to_numpy())
        examples.append(features)
    return pd.DataFrame(examples).sort_values("anchor_date").reset_index(drop=True) if examples else pd.DataFrame()


def _training_examples(pool: pd.DataFrame, origin: pd.Timestamp, horizon: int, capacity: float) -> pd.DataFrame:
    """Select labels known at this origin from the precomputed feature pool."""
    if pool.empty:
        return pd.DataFrame()
    examples: list[dict[str, float | pd.Timestamp]] = []
    for row in pool.itertuples(index=False):
        available = [
            (pd.Timestamp(date), float(value))
            for date, value in zip(row.candidate_dates, row.candidate_values)
            if pd.Timestamp(date) < origin
        ]
        if not available:
            continue
        label_date, label_value = min(
            available, key=lambda item: (abs((item[0] - row.anchor_date).days - horizon), item[0])
        )
        features = row._asdict()
        for column in ("candidate_dates", "candidate_values"):
            features.pop(column, None)
        features["lead_days"] = float((label_date - row.anchor_date).days)
        target_month_angle = 2.0 * np.pi * label_date.month / 12.0
        target_year_angle = 2.0 * np.pi * label_date.dayofyear / (366.0 if label_date.is_leap_year else 365.0)
        features["target_month_sin"] = float(np.sin(target_month_angle))
        features["target_month_cos"] = float(np.cos(target_month_angle))
        features["target_year_sin"] = float(np.sin(target_year_angle))
        features["target_year_cos"] = float(np.cos(target_year_angle))
        features["label_date"] = label_date
        features["target_delta_fraction"] = (label_value - float(row.anchor_storage_mcft)) / capacity
        examples.append(features)
    return pd.DataFrame(examples).sort_values("anchor_date").reset_index(drop=True) if examples else pd.DataFrame()


def _choose_config(examples: pd.DataFrame, feature_columns: list[str], origin: pd.Timestamp) -> dict[str, float | int | str]:
    """Select hyperparameters on a chronological tail split, then refit all rows."""
    if len(examples) < 60:
        return MODEL_CONFIGS[0]
    validation_start = max(30, int(len(examples) * 0.8))
    validation_anchor = pd.Timestamp(examples.iloc[validation_start]["anchor_date"])
    train = examples[examples["label_date"] < validation_anchor]
    valid = examples.iloc[validation_start:]
    if len(train) < 25 or len(valid) < 8:
        return MODEL_CONFIGS[0]
    x_train = train[feature_columns]
    y_train = train["target_delta_fraction"].to_numpy(dtype=float)
    x_valid = valid[feature_columns]
    y_valid = valid["target_delta_fraction"].to_numpy(dtype=float)
    best_config = MODEL_CONFIGS[0]
    best_rmse = float("inf")
    for config in MODEL_CONFIGS:
        estimator = _make_estimator(config)
        weights = _sample_weight(train["anchor_date"], origin, float(config["half_life_years"]))
        estimator.fit(x_train, y_train, sample_weight=weights)
        prediction = estimator.predict(x_valid)
        score = float(np.sqrt(np.mean(np.square(prediction - y_valid))))
        # Earlier config wins ties, favoring the shallower model.
        if score < best_rmse - 1e-12:
            best_rmse = score
            best_config = config
    return best_config


def xgboost_prediction(
    history: pd.Series,
    origin: pd.Timestamp,
    target: pd.Timestamp,
    capacity: float,
    satellite_history: pd.Series | None = None,
    training_pool: pd.DataFrame | None = None,
    config_cache: dict[str, dict[str, float | int | str]] | None = None,
    config_key: str = "xgboost",
    rainfall_history: pd.Series | None = None,
) -> float | None:
    """Predict storage change with chronologically tuned XGBoost."""
    known = history[history.index <= origin].dropna().sort_index()
    if known.empty:
        return None
    horizon = (target - origin).days
    if training_pool is None:
        training_pool = build_xgb_training_pool(
            known, horizon, 3, capacity, satellite_history, rainfall_history
        )
    examples = _training_examples(training_pool, origin, horizon, capacity)
    if len(examples) < 24:
        return None
    excluded = {"anchor_date", "label_date", "target_delta_fraction", "anchor_storage_mcft"}
    feature_columns = [column for column in examples.columns if column not in excluded]
    quarter_key = f"{config_key}:{origin.year}-Q{origin.quarter}"
    if config_cache is not None and quarter_key in config_cache:
        config = config_cache[quarter_key]
    else:
        config = _choose_config(examples, feature_columns, origin)
        if config_cache is not None:
            config_cache[quarter_key] = config
    estimator = _make_estimator(config)
    weights = _sample_weight(examples["anchor_date"], origin, float(config["half_life_years"]))
    estimator.fit(examples[feature_columns], examples["target_delta_fraction"].to_numpy(dtype=float), sample_weight=weights)
    latest_value = float(known.iloc[-1])
    test_features = pd.DataFrame([
        xgb_features(known, origin, target, capacity, satellite_history, rainfall_history)
    ], columns=feature_columns)
    delta_fraction = float(estimator.predict(test_features)[0])
    return float(np.clip(latest_value + delta_fraction * capacity, 0.0, capacity))

