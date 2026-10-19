import os
import warnings

import numpy as np
import pandas as pd
from scipy.optimize import OptimizeWarning, curve_fit
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import TimeSeriesSplit

warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=OptimizeWarning)

RESERVOIRS = ["poondi", "cholavaram", "puzhal", "chembarambakkam", "veeranam"]
ALIASES = {"puzhal": ["puzhal", "redhills", "red hills"]}
CLOUD_MAX = 25
DAYFIRST = True
GROUND_TRUTH = os.environ.get("AQUASAT_GROUND_TRUTH", "reviewed_ground_truth.csv")
# This is a model-comparison pipeline; it must not silently overwrite the
# active clear95 dashboard outputs. Use build_clear95_dashboard.py for those.
DASHBOARD_OUTPUT = os.environ.get("AQUASAT_DASHBOARD_OUTPUT", "dashboard_data_model_comparison_candidate.csv")
METRICS_OUTPUT = os.environ.get("AQUASAT_METRICS_OUTPUT", "metrics_model_comparison_candidate.csv")
SATELLITE_SUFFIX = os.environ.get("AQUASAT_SATELLITE_SUFFIX", "")
INCLUDE_LEGACY_DROUGHT = os.environ.get("AQUASAT_INCLUDE_LEGACY_DROUGHT", "0").strip() == "1"
EXCLUDE_DROUGHT = set()
FORCE_MODEL = {}
N_SPLITS = min(5, max(2, int(os.environ.get("AQUASAT_CV_SPLITS", "5"))))
LOW_STORAGE = 300
MIN_MATCHED = 15
EXPECTED_INDEX = {"chembarambakkam": "mndwi"}
EXPECTED_MIN_MAX_HA = {"cholavaram": 300}
MAX_FIT_STORAGE = {"veeranam": 600}
CAPACITY = {
    "poondi": 3231.0,
    "cholavaram": 1081.0,
    "puzhal": 3300.0,
    "chembarambakkam": 3645.0,
    "veeranam": 1465.0,
}


def power_law(x, a, b):
    return a * np.power(x, b)


def offset_power_law(x, a, b, c):
    return a * np.power(np.clip(x - c, 0, None), b)


class PowerLawModel:
    def fit(self, x, y):
        self.params, _ = curve_fit(
            power_law,
            np.asarray(x, dtype=float),
            np.asarray(y, dtype=float),
            p0=[0.004, 1.7],
            bounds=([0, 0.1], [np.inf, 5.0]),
            maxfev=50000,
        )
        return self

    def predict(self, x):
        return np.clip(power_law(np.asarray(x, dtype=float), *self.params), 0, None)


class OffsetPowerLawModel:
    def fit(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        a0, b0 = PowerLawModel().fit(x, y).params
        upper = max(float(np.percentile(x, 60)), 1.0)
        c0 = min(max(float(x.min()) * 0.5, 0.01), upper * 0.5)
        self.params, _ = curve_fit(
            offset_power_law,
            x,
            y,
            p0=[a0, b0, c0],
            bounds=([0, 0.1, 0], [np.inf, 5.0, upper]),
            maxfev=50000,
        )
        return self

    def predict(self, x):
        return np.clip(offset_power_law(np.asarray(x, dtype=float), *self.params), 0, None)


class GPModel:
    def fit(self, x, y):
        x = np.asarray(x, dtype=float)
        self.mu = x.mean()
        self.sd = x.std() or 1.0
        kernel = ConstantKernel(1.0) * RBF(length_scale=1.0, length_scale_bounds=(0.1, 10.0)) + WhiteKernel(noise_level=0.1, noise_level_bounds=(1e-3, 1.0))
        # One initialization keeps chronological evaluation responsive while
        # retaining deterministic kernel optimization.
        self.gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=1, random_state=42)
        self.gp.fit(((x - self.mu) / self.sd).reshape(-1, 1), np.asarray(y, dtype=float))
        return self

    def predict(self, x):
        x = np.asarray(x, dtype=float)
        return np.clip(self.gp.predict(((x - self.mu) / self.sd).reshape(-1, 1)), 0, None)


class IsotonicModel:
    """Monotonic, nonparametric area-to-storage calibration."""

    def fit(self, x, y):
        self.model = IsotonicRegression(y_min=0.0, out_of_bounds="clip")
        self.model.fit(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        return self

    def predict(self, x):
        return np.clip(self.model.predict(np.asarray(x, dtype=float)), 0, None)


MODELS = {
    "Power-law": PowerLawModel,
    "Offset power-law": OffsetPowerLawModel,
    "Gaussian Process": GPModel,
    "Isotonic regression": IsotonicModel,
}


def load_ground_truth():
    wide = pd.read_csv(GROUND_TRUTH)
    date_col = wide.columns[0]
    # The scraper writes ISO dates while older manual rows use DD-MM-YYYY.
    # Parse each cell independently so a new ISO row does not invalidate the
    # historical date format inferred from the first row.
    dates = pd.to_datetime(wide[date_col], dayfirst=DAYFIRST, errors="coerce", format="mixed")
    frames = []
    for key in RESERVOIRS:
        names = ALIASES.get(key, [key])
        col = next((c for c in wide.columns[1:] if any(n in c.lower() for n in names)), None)
        if col is None:
            print(f"no ground-truth column found for {key}")
            continue
        values = pd.to_numeric(wide[col].astype(str).str.replace(",", "", regex=False), errors="coerce")
        f = pd.DataFrame({"reservoir": key, "date": dates, "official_mcft": values})
        frames.append(f.dropna(subset=["date", "official_mcft"]))
    return pd.concat(frames, ignore_index=True)


def load_monthly(key):
    df = pd.read_csv(f"{key}_satellite_areas{SATELLITE_SUFFIX}.csv")
    df["date"] = pd.to_datetime(df["image_ymd"].astype(str))
    cloud_col = "cloud_percent" if "cloud_percent" in df.columns else "scene_cloud_percent"
    df = df[df[cloud_col] <= CLOUD_MAX]
    df = df.rename(columns={"water_area_hectares": "area_ha"})
    return df[["date", "area_ha"]].dropna()


def load_drought(key):
    path = f"{key}_drought_candidates.csv"
    if key in EXCLUDE_DROUGHT or not os.path.exists(path):
        return pd.DataFrame({"date": pd.to_datetime([]), "area_ha": pd.Series(dtype=float)})
    df = pd.read_csv(path)
    cloud_col = "cloud_percent" if "cloud_percent" in df.columns else "cloud_pct"
    df["date"] = pd.to_datetime(df["ymd"].astype(str), format="%Y%m%d")
    df = df[df[cloud_col] <= CLOUD_MAX]
    df = df.rename(columns={"water_area_ha": "area_ha"})
    return df[["date", "area_ha"]].dropna()


def load_satellite(key):
    monthly = load_monthly(key)
    # Legacy drought supplements were measured with a different, unrecorded
    # processing path, so they are excluded unless explicitly requested.
    parts = [monthly]
    if not SATELLITE_SUFFIX and INCLUDE_LEGACY_DROUGHT and key not in EXCLUDE_DROUGHT:
        parts.append(load_drought(key))
    both = pd.concat(parts, ignore_index=True)
    return both.sort_values("date").drop_duplicates("date", keep="first").reset_index(drop=True)


def check_inputs():
    problems = []
    for key in RESERVOIRS:
        df = pd.read_csv(f"{key}_satellite_areas{SATELLITE_SUFFIX}.csv")
        index = str(df["water_index"].iloc[0]).lower() if "water_index" in df.columns and len(df) else "ndwi"
        expected = EXPECTED_INDEX.get(key, "ndwi")
        if index != expected:
            problems.append(f"{key}: satellite file uses {index}, expected {expected}")
        floor = EXPECTED_MIN_MAX_HA.get(key)
        largest = df["water_area_hectares"].max()
        if floor and largest < floor:
            problems.append(f"{key}: largest satellite area is {largest:.0f} ha, expected at least {floor}; review its boundary and extraction provenance")
    return problems


def cv_errors(Model, matched):
    # Evaluate later satellite/official matches using only earlier matches.
    # This is a calibration holdout, not a forecast-horizon backtest. Random
    # folds leak nearby seasonal observations and can overstate performance.
    matched = matched.sort_values("date").reset_index(drop=True)
    x = matched["area_ha"].to_numpy()
    y = matched["official_mcft"].to_numpy()
    # Keep at least a small initial training window for reservoirs with fewer
    # labels, while using up to five chronological validation windows.
    n_splits = min(N_SPLITS, max(2, len(x) // 6))
    splitter = TimeSeriesSplit(n_splits=n_splits)
    errors, actual = [], []
    for train_idx, test_idx in splitter.split(x):
        try:
            model = Model().fit(x[train_idx], y[train_idx])
        except (RuntimeError, ValueError):
            continue
        errors.append(model.predict(x[test_idx]) - y[test_idx])
        actual.append(y[test_idx])
    if not errors:
        return None
    return np.concatenate(errors), np.concatenate(actual)


def summarize(result):
    if result is None:
        return None
    err, actual = result
    low = actual < LOW_STORAGE
    rmse = float(np.sqrt(np.mean(err**2)))
    low_rmse = float(np.sqrt(np.mean(err[low] ** 2))) if low.any() else rmse
    return {
        "rmse": rmse,
        "bias": float(err.mean()),
        "low_bias": float(err[low].mean()) if low.any() else float("nan"),
        "low_rmse": low_rmse,
        "score": (rmse + low_rmse) / 2,
    }


def main():
    problems = check_inputs()
    if problems:
        print("Stale or mismatched input files, nothing was written:")
        for line in problems:
            print("  " + line)
        raise SystemExit(1)
    truth = load_ground_truth()
    parts = []
    metrics = []

    for key in RESERVOIRS:
        sat = load_satellite(key)
        off = truth[truth["reservoir"] == key][["date", "official_mcft"]].drop_duplicates("date")
        matched = sat.merge(off, on="date").sort_values("date").reset_index(drop=True)
        limit = MAX_FIT_STORAGE.get(key)
        if limit:
            matched = matched[matched["official_mcft"] <= limit]
        drought_rows = (
            len(load_drought(key))
            if not SATELLITE_SUFFIX and INCLUDE_LEGACY_DROUGHT and key not in EXCLUDE_DROUGHT
            else 0
        )
        print(f"{key}: {len(sat)} satellite rows ({drought_rows} drought), {len(off)} official rows, {len(matched)} matched")
        if len(matched) < MIN_MATCHED:
            print("  too few matched rows, skipping")
            continue
        results = {name: summarize(cv_errors(M, matched)) for name, M in MODELS.items()}
        valid = {n: r for n, r in results.items() if r is not None}
        if not valid:
            print("  no model could be fitted, skipping")
            continue
        for name, r in valid.items():
            print(f"  {name:<17} CV RMSE {r['rmse']:7.1f}  bias {r['bias']:+7.1f}  low-storage RMSE {r['low_rmse']:7.1f}  low-storage bias {r['low_bias']:+7.1f}")
        forced = FORCE_MODEL.get(key)
        chosen = forced if forced in valid else min(valid, key=lambda n: valid[n]["score"])
        print(f"  chosen: {chosen}")
        best = valid[chosen]
        metrics.append(
            {
                "reservoir": key,
                "valid_up_to_mcft": limit if limit else "",
                "model": chosen,
                "rmse": round(best["rmse"], 1),
                "bias": round(best["bias"], 1),
                "low_storage_bias": round(best["low_bias"], 1) if not np.isnan(best["low_bias"]) else "",
                "n_matched": len(matched),
                "cv_method": "expanding-window chronological",
                "cv_splits": min(N_SPLITS, max(2, len(matched) // 6)),
                "rmse_power_law": round(valid["Power-law"]["rmse"], 1) if "Power-law" in valid else "",
                "rmse_offset_power_law": round(valid["Offset power-law"]["rmse"], 1) if "Offset power-law" in valid else "",
                "rmse_gp": round(valid["Gaussian Process"]["rmse"], 1) if "Gaussian Process" in valid else "",
            }
        )
        final = MODELS[chosen]().fit(matched["area_ha"], matched["official_mcft"])
        sat["sat_estimate_mcft"] = np.clip(
            final.predict(sat["area_ha"]),
            0,
            min(limit, CAPACITY[key]) if limit else CAPACITY[key],
        )
        merged = sat[["date", "sat_estimate_mcft"]].merge(off, on="date", how="outer")
        merged["reservoir"] = key
        parts.append(merged)

    out = pd.concat(parts, ignore_index=True)[["reservoir", "date", "sat_estimate_mcft", "official_mcft"]]
    out = out.sort_values(["reservoir", "date"])
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(DASHBOARD_OUTPUT, index=False)
    pd.DataFrame(metrics).to_csv(METRICS_OUTPUT, index=False)
    print(f"wrote {DASHBOARD_OUTPUT} ({len(out)} rows) and {METRICS_OUTPUT}")


if __name__ == "__main__":
    main()
