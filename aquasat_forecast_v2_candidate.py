"""Pooled cross-lake XGBoost forecast candidate based on Claude's experiment.

This is a research candidate only. It does not write live forecasts.
All storage and rainfall features are computed using information available on
or before their anchor date. Training labels are filtered by the backtest's
forecast origin.
"""

from __future__ import annotations

import os
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
LAKES = tuple(CAPACITY)
LAGS = (1, 3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 365)
TRAIN_LEADS = (12, 13, 14, 15, 16, 17, 18)
RAINFALL_WINDOWS = (7, 30, 90)


def read_rainfall_histories(path: str | Path) -> dict[str, pd.Series]:
    """Read nearest-cell daily rainfall without filling missing days with zero."""
    frame = pd.read_csv(path, parse_dates=["date"])
    required = {"date", "reservoir", "rainfall_mm"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Rainfall CSV is missing columns: {', '.join(sorted(missing))}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["reservoir"] = frame["reservoir"].astype(str).str.strip().str.lower()
    frame["rainfall_mm"] = pd.to_numeric(frame["rainfall_mm"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    histories: dict[str, pd.Series] = {}
    for lake in LAKES:
        group = frame[frame["reservoir"] == lake].sort_values("date")
        group = group.drop_duplicates("date", keep="last")
        if group.empty:
            raise ValueError(f"No rainfall history found for {lake} in {path}.")
        histories[lake] = pd.Series(
            group["rainfall_mm"].to_numpy(dtype=float),
            index=pd.DatetimeIndex(group["date"]),
            name=lake,
        ).sort_index()
    return histories


def daily_fraction_frame(
    series_by_lake: dict[str, pd.Series], end: pd.Timestamp | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = min(series.index.min() for series in series_by_lake.values() if not series.empty)
    stop = end if end is not None else max(
        series.index.max() for series in series_by_lake.values() if not series.empty
    )
    index = pd.date_range(start, stop, freq="D")
    raw = pd.DataFrame(index=index)
    for lake in LAKES:
        if lake not in series_by_lake:
            continue
        series = series_by_lake[lake].copy()
        series.index = pd.to_datetime(series.index).normalize()
        raw[lake] = series.reindex(index) / CAPACITY[lake]
    return raw, raw.ffill(limit=10)


def _rainfall_total(
    history: pd.Series | None, anchor: pd.Timestamp, days: int
) -> float:
    if history is None or history.empty:
        return float("nan")
    start = anchor - pd.Timedelta(days=days)
    window = history[(history.index >= start) & (history.index < anchor)]
    if len(window) != days or window.index.nunique() != days or window.isna().any():
        return float("nan")
    return float(window.sum())


def _lake_block(
    lake: str,
    filled: pd.DataFrame,
    rainfall_histories: dict[str, pd.Series] | None = None,
    rainfall_windows: tuple[int, ...] = RAINFALL_WINDOWS,
    satellite_histories: dict[str, pd.Series] | None = None,
    raw: pd.DataFrame | None = None,
) -> pd.DataFrame:
    f = filled[lake]
    block = pd.DataFrame(index=filled.index)
    block["frac"] = f
    for k in LAGS:
        block[f"d{k}"] = f - f.shift(k)
    for window in (90, 365):
        block[f"from_max{window}"] = f - f.rolling(window, min_periods=20).max()
        block[f"from_min{window}"] = f - f.rolling(window, min_periods=20).min()
    for window in (14, 30, 90):
        block[f"std{window}"] = f.rolling(window, min_periods=5).std()
    block["slope7"] = (f - f.shift(7)) / 7.0
    block["slope30"] = (f - f.shift(30)) / 30.0
    block["accel"] = block["d7"] - (f.shift(7) - f.shift(14))
    other = filled[[column for column in filled.columns if column != lake]]
    block["others_mean"] = other.mean(axis=1)
    block["others_d7"] = (other - other.shift(7)).mean(axis=1)
    block["others_d14"] = (other - other.shift(14)).mean(axis=1)
    block["others_d30"] = (other - other.shift(30)).mean(axis=1)
    block["others_rising"] = ((other - other.shift(7)) > 0.003).mean(axis=1)
    block["rel_to_others"] = f - block["others_mean"]
    block["lake_id"] = float(LAKES.index(lake))
    block["log_capacity"] = float(np.log(CAPACITY[lake]))
    doy = filled.index.dayofyear.to_numpy(dtype=float)
    block["anchor_sin"] = np.sin(2 * np.pi * doy / 365.25)
    block["anchor_cos"] = np.cos(2 * np.pi * doy / 365.25)

    if rainfall_histories is not None:
        rainfall = rainfall_histories.get(lake)
        if rainfall is not None and not rainfall.empty:
            rainfall = rainfall.copy()
            rainfall.index = pd.to_datetime(rainfall.index).normalize()
            warmup_start = min(pd.Timestamp(filled.index.min()), pd.Timestamp(rainfall.index.min()))
            rainfall_index = pd.date_range(warmup_start, filled.index.max(), freq="D")
            rain_daily = rainfall.reindex(rainfall_index).shift(1)
        else:
            rain_daily = None
        for days in rainfall_windows:
            if rain_daily is None:
                block[f"rainfall_{days}d_mm"] = np.nan
            else:
                # Shift first so the window ends yesterday, excluding anchor-day rain.
                rolling = rain_daily.rolling(days, min_periods=days).sum()
                block[f"rainfall_{days}d_mm"] = rolling.reindex(filled.index)

    if satellite_histories is not None:
        satellite = satellite_histories.get(lake)
        if satellite is None or satellite.empty:
            satellite_on_anchor = pd.Series(np.nan, index=filled.index, dtype=float)
            satellite_dates = pd.Series(pd.NaT, index=filled.index, dtype="datetime64[ns]")
        else:
            satellite = satellite.copy()
            satellite.index = pd.to_datetime(satellite.index).normalize()
            satellite = pd.to_numeric(satellite, errors="coerce").dropna().sort_index()
            satellite = satellite[~satellite.index.duplicated(keep="last")]
            anchors = pd.DataFrame({"anchor_date": filled.index})
            estimates = satellite.rename("satellite_estimate_mcft").rename_axis("satellite_date").reset_index()
            matched = pd.merge_asof(
                anchors.sort_values("anchor_date"),
                estimates.sort_values("satellite_date"),
                left_on="anchor_date",
                right_on="satellite_date",
                direction="backward",
                allow_exact_matches=True,
            ).set_index("anchor_date")
            satellite_on_anchor = matched["satellite_estimate_mcft"].reindex(filled.index)
            satellite_dates = matched["satellite_date"].reindex(filled.index)

        if raw is None or lake not in raw:
            raise ValueError("Official raw storage is required when satellite features are enabled.")
        # Match the live XGBoost satellite features: use the latest official
        # reading and satellite estimate available on or before each anchor.
        latest_official_fraction = raw[lake].reindex(filled.index).ffill()
        satellite_fraction = satellite_on_anchor / CAPACITY[lake]
        block["satellite_available"] = satellite_on_anchor.notna().astype(float)
        block["satellite_storage_fraction"] = satellite_fraction
        block["satellite_minus_official_fraction"] = satellite_fraction - latest_official_fraction
        block["satellite_age_days"] = (
            pd.Series(filled.index, index=filled.index) - satellite_dates
        ).dt.days.fillna(9999.0).astype(float)
    return block


def _add_target_season(frame: pd.DataFrame, target_dates: pd.DatetimeIndex) -> pd.DataFrame:
    doy = target_dates.dayofyear.to_numpy(dtype=float)
    frame["target_sin"] = np.sin(2 * np.pi * doy / 365.25)
    frame["target_cos"] = np.cos(2 * np.pi * doy / 365.25)
    frame["target_sin2"] = np.sin(4 * np.pi * doy / 365.25)
    frame["target_cos2"] = np.cos(4 * np.pi * doy / 365.25)
    return frame


def build_feature_table(
    raw: pd.DataFrame,
    filled: pd.DataFrame,
    leads: tuple[int, ...] = TRAIN_LEADS,
    stride: int = 2,
    rainfall_histories: dict[str, pd.Series] | None = None,
    rainfall_windows: tuple[int, ...] = RAINFALL_WINDOWS,
    satellite_histories: dict[str, pd.Series] | None = None,
) -> pd.DataFrame:
    """Build shared examples; histories at each anchor contain no future fill."""
    parts: list[pd.DataFrame] = []
    for lake in raw.columns:
        block = _lake_block(
            lake, filled, rainfall_histories, rainfall_windows, satellite_histories, raw
        )
        block = block[raw[lake].notna()].iloc[::stride]
        for lead in leads:
            target_dates = block.index + pd.Timedelta(days=lead)
            labels = raw[lake].reindex(target_dates).to_numpy()
            part = block.copy()
            part["lead"] = float(lead)
            part = _add_target_season(part, target_dates)
            part["target_delta"] = labels - part["frac"].to_numpy()
            part["label_date"] = target_dates
            part["anchor_date"] = block.index
            part["lake"] = lake
            parts.append(part)
    if not parts:
        raise ValueError("No training examples could be created from official storage.")
    table = pd.concat(parts, ignore_index=True)
    return table.dropna(subset=["target_delta"]).reset_index(drop=True)


def feature_columns(table: pd.DataFrame) -> list[str]:
    excluded = {"target_delta", "label_date", "anchor_date", "lake"}
    return [column for column in table.columns if column not in excluded]


def make_model():
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise SystemExit(
            "XGBoost is required. Install the research dependencies with "
            "python -m pip install -r requirements-research.txt"
        ) from exc
    # Frozen to Claude's reported best configuration. Do not tune on the
    # locked post-August-2026 holdout.
    params = dict(
        n_estimators=350,
        max_depth=4,
        learning_rate=0.03,
        subsample=0.7,
        colsample_bytree=0.6,
        min_child_weight=30,
        reg_lambda=20,
        reg_alpha=0.5,
        tree_method="hist",
        n_jobs=2,
        random_state=1,
        verbosity=0,
    )
    device = os.environ.get("AQUASAT_XGB_DEVICE", "cpu").strip().lower()
    if device != "cpu":
        params["device"] = device
    return xgb.XGBRegressor(**params)


def recency_weights(
    anchor_dates: pd.Series, origin: pd.Timestamp, half_life_years: float = 5.0
) -> np.ndarray:
    age = (origin - pd.to_datetime(anchor_dates)).dt.days.to_numpy(dtype=float) / 365.25
    return np.power(0.5, np.maximum(age, 0.0) / half_life_years)


def fit_model(
    table: pd.DataFrame, origin: pd.Timestamp
) -> tuple[object, list[str], int]:
    """Fit only rows whose anchor and target labels precede the origin."""
    origin = pd.Timestamp(origin)
    train = table[(table["label_date"] < origin) & (table["anchor_date"] < origin)]
    if train.empty:
        raise ValueError(f"No eligible v2 training rows before {origin:%Y-%m-%d}.")
    columns = feature_columns(table)
    model = make_model()
    model.fit(
        train[columns].to_numpy(dtype=np.float32),
        train["target_delta"].to_numpy(dtype=float),
        sample_weight=recency_weights(train["anchor_date"], origin),
    )
    return model, columns, len(train)


def predict_storage(
    model: object,
    columns: list[str],
    raw: pd.DataFrame,
    filled: pd.DataFrame,
    lake: str,
    origin: pd.Timestamp,
    target: pd.Timestamp,
    rainfall_histories: dict[str, pd.Series] | None = None,
    rainfall_windows: tuple[int, ...] = RAINFALL_WINDOWS,
    satellite_histories: dict[str, pd.Series] | None = None,
) -> float:
    origin, target = pd.Timestamp(origin), pd.Timestamp(target)
    available = filled.loc[:origin]
    if origin not in available.index:
        raise ValueError(f"Origin {origin:%Y-%m-%d} is outside the daily feature index.")
    block = _lake_block(
        lake, available, rainfall_histories, rainfall_windows, satellite_histories, raw
    )
    row = block.loc[[origin]].copy()
    row["lead"] = float((target - origin).days)
    row = _add_target_season(row, pd.DatetimeIndex([target]))
    prediction = float(model.predict(row[columns].to_numpy(dtype=np.float32))[0])
    latest = raw[lake].loc[:origin].dropna()
    if latest.empty:
        raise ValueError(f"No official storage is available for {lake} at {origin:%Y-%m-%d}.")
    # ``raw`` stores capacity-normalized fractions, while the public forecast
    # and target values are in mcft. Convert the latest storage before adding
    # the model's predicted fractional change.
    latest_storage_mcft = float(latest.iloc[-1]) * CAPACITY[lake]
    return float(np.clip(
        latest_storage_mcft + prediction * CAPACITY[lake],
        0.0,
        CAPACITY[lake],
    ))

