import os

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="AquaSat Chennai", page_icon="💧", layout="wide")

RESERVOIRS = {
    "poondi": ("Poondi", 3231),
    "cholavaram": ("Cholavaram", 1081),
    "puzhal": ("Puzhal (Redhills)", 3300),
    "chembarambakkam": ("Chembarambakkam", 3645),
    "veeranam": ("Veeranam", 1465),
}

STATUS = {
    "ok": ("Normal", "#2E7D5B"),
    "watch": ("Watch", "#C98A1B"),
    "critical": ("Low", "#B3372E"),
    "unknown": ("No forecast", "#7A8B91"),
}

SAT_COLOR = "#1B6CA8"
OFFICIAL_COLOR = "#D9622B"
FORECAST_COLOR = "#0E3B47"
BAND_COLOR = "rgba(14, 59, 71, 0.16)"
STALE_DAYS = 30
API_BASE_URL = os.environ.get("AQUASAT_API_URL", "http://127.0.0.1:8765").rstrip("/")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stApp { font-family: 'Public Sans', sans-serif; }
.stApp { background: #F4F8F9; }
h1, h2, h3 { color: #0E3B47; font-weight: 700; letter-spacing: -0.01em; }
.card { background: #FFFFFF; border: 1px solid #D5E3E7; border-radius: 10px; padding: 16px 18px; height: 100%; }
.card h4 { margin: 0 0 6px 0; font-size: 1.05rem; color: #0E3B47; font-weight: 600; }
.pct { font-size: 2rem; font-weight: 700; color: #0E3B47; line-height: 1.1; }
.sub { color: #5A737B; font-size: 0.85rem; line-height: 1.4; }
.pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600; color: #FFFFFF; margin-left: 6px; }
.tank { position: relative; height: 14px; background: #E3EEF1; border-radius: 7px; margin: 12px 0 10px 0; }
.tank .fill { height: 100%; border-radius: 7px; }
.tank .tick { position: absolute; top: -4px; width: 3px; height: 22px; background: #0E3B47; border-radius: 2px; }
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


def canon(name):
    n = str(name).strip().lower()
    if n in ("redhills", "red hills"):
        return "puzhal"
    return n


@st.cache_data
def load_history(file_mtime):
    df = pd.read_csv("dashboard_data.csv", parse_dates=["date"])
    df["key"] = df["reservoir"].map(canon)
    return df.sort_values("date")


@st.cache_data
def load_forecast(file_mtime):
    df = pd.read_csv("reservoir_forecasts.csv", parse_dates=["ds"])
    df["key"] = df["reservoir"].map(canon)
    # Never render expired rows as a future outlook. The legacy file has no
    # generation timestamp, so only dates on or after today can be displayed.
    df = df[df["ds"] >= pd.Timestamp.today().normalize()]
    return df.sort_values("ds")


@st.cache_data
def load_metrics(file_mtime):
    rows = {
        k: {
            "model": "n/a", "rmse": None, "bias": None,
            "low_storage_rmse": None, "low_storage_pairs": None,
            "n_matched": None, "validation_source": None,
        }
        for k in RESERVOIRS
    }
    if os.path.exists("metrics.csv"):
        m = pd.read_csv("metrics.csv")
        for _, r in m.iterrows():
            k = canon(r["reservoir"])
            entry = rows.setdefault(k, {"model": "", "rmse": None, "bias": None})
            if "model" in m.columns and pd.notna(r["model"]):
                entry["model"] = r["model"]
            if "rmse" in m.columns and pd.notna(r["rmse"]):
                entry["rmse"] = float(r["rmse"])
            if "bias" in m.columns and pd.notna(r["bias"]):
                entry["bias"] = float(r["bias"])
            if "valid_up_to_mcft" in m.columns and pd.notna(r["valid_up_to_mcft"]):
                entry["valid_up_to"] = float(r["valid_up_to_mcft"])
            if "cv_method" in m.columns and pd.notna(r["cv_method"]):
                entry["cv_method"] = str(r["cv_method"])
            for column in ("low_storage_rmse", "low_storage_pairs", "n_matched"):
                if column in m.columns and pd.notna(r[column]):
                    entry[column] = float(r[column])
            if "validation_source" in m.columns and pd.notna(r["validation_source"]):
                entry["validation_source"] = str(r["validation_source"])
    return rows


@st.cache_data(ttl=30)
def load_dashboard_from_api(api_base_url):
    """Load core dashboard data from AquaSat's local REST API."""
    response = requests.get(f"{api_base_url}/api/v1/dashboard", timeout=5)
    response.raise_for_status()
    payload = response.json()
    required = {"history", "forecasts", "metrics", "data_status"}
    missing = required.difference(payload)
    if missing:
        raise ValueError("AquaSat API response is missing: " + ", ".join(sorted(missing)))
    return payload


def load_threshold_review():
    """Load optional out-of-sample threshold-comparison predictions."""
    path = "sentinel2_v3_clear95_threshold_comparison_v2_candidate.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["date"])
    required = {
        "date", "reservoir", "official_storage_mcft", "split",
        "selected_from_early_dates", "current_baseline_prediction_mcft",
        "ndwi_p000_prediction_mcft", "mndwi_p000_prediction_mcft",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            "Threshold comparison file is missing columns: "
            + ", ".join(sorted(missing))
        )
    df["key"] = df["reservoir"].map(canon)
    return df


def load_sentinel1_review():
    """Load optional out-of-sample Sentinel-1 fallback candidate rows."""
    path = "sentinel1_v3_oos_fallback_candidate.csv"
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["date"])
    required = {
        "date", "reservoir", "satellite_estimate_mcft", "official_storage_mcft",
        "estimate_error_mcft", "selection_cutoff_date", "selected_area_variant",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(
            "Sentinel-1 candidate file is missing columns: "
            + ", ".join(sorted(missing))
        )
    df["key"] = df["reservoir"].map(canon)
    for column in ("satellite_estimate_mcft", "official_storage_mcft", "estimate_error_mcft"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


@st.cache_data
def load_rainfall_history(path, file_mtime):
    """Load the historical IMD nearest-grid-cell rainfall case study."""
    df = pd.read_csv(path, parse_dates=["date"])
    required = {"date", "reservoir", "rainfall_mm", "grid_offset_km", "spatial_support"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("Rainfall CSV is missing columns: " + ", ".join(sorted(missing)))
    df["key"] = df["reservoir"].map(canon)
    df["rainfall_mm"] = pd.to_numeric(df["rainfall_mm"], errors="coerce")
    df["grid_offset_km"] = pd.to_numeric(df["grid_offset_km"], errors="coerce")
    return df


@st.cache_data
def load_rainfall_backtest(file_mtime):
    """Load the fixed-origin 2019 comparison; never used by the live forecast."""
    df = pd.read_csv("rainfall_2019_crisis_summary.csv")
    required = {
        "reservoir", "pairs", "baseline_rmse_mcft", "rainfall_rmse_mcft",
        "rmse_change_mcft", "baseline_rmse_pct_capacity",
        "rainfall_rmse_pct_capacity", "rmse_change_pct_capacity",
        "baseline_mae_mcft", "rainfall_mae_mcft",
        "below_300_pairs", "baseline_below_300_rmse_mcft",
        "rainfall_below_300_rmse_mcft",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("Rainfall summary is missing columns: " + ", ".join(sorted(missing)))
    df["key"] = df["reservoir"].map(canon)
    return df


@st.cache_data
def load_rainfall_predictions(file_mtime):
    """Load exact, conflict-screened target rows used by the rainfall backtest."""
    df = pd.read_csv("rainfall_2019_crisis_predictions.csv", parse_dates=["target_date"])
    required = {"reservoir", "target_date", "actual_mcft"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("Rainfall predictions are missing columns: " + ", ".join(sorted(missing)))
    df["key"] = df["reservoir"].map(canon)
    df["actual_mcft"] = pd.to_numeric(df["actual_mcft"], errors="coerce")
    return df


@st.cache_data
def load_rainfall_multiyear_summary(file_mtime):
    """Load the separate multi-year rainfall candidate summary."""
    df = pd.read_csv("rainfall_2019_2024_summary_candidate.csv")
    required = {
        "reservoir", "pairs", "baseline_rmse_mcft", "rainfall_rmse_mcft",
        "rmse_change_mcft", "baseline_rmse_pct_capacity",
        "rainfall_rmse_pct_capacity", "rmse_change_pct_capacity",
        "below_300_pairs", "baseline_below_300_rmse_mcft",
        "rainfall_below_300_rmse_mcft",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("Multi-year rainfall summary is missing columns: " + ", ".join(sorted(missing)))
    return df


@st.cache_data
def load_rainfall_yearly_summary(file_mtime):
    """Load per-target-year/per-reservoir rainfall candidate scores."""
    df = pd.read_csv("rainfall_2019_2024_by_year_candidate.csv")
    required = {
        "target_year", "reservoir", "pairs", "baseline_rmse_mcft",
        "rainfall_rmse_mcft", "rmse_change_mcft",
        "baseline_rmse_pct_capacity", "rainfall_rmse_pct_capacity",
        "rmse_change_pct_capacity",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("Yearly rainfall summary is missing columns: " + ", ".join(sorted(missing)))
    df["target_year"] = pd.to_numeric(df["target_year"], errors="coerce")
    return df.dropna(subset=["target_year"])


def describe_threshold_variant(variant):
    parts = str(variant).rsplit("_", 1)
    if len(parts) != 2 or len(parts[1]) < 2:
        return str(variant)
    index, token = parts
    sign = -1 if token[0] == "m" else 1
    try:
        threshold_value = sign * int(token[1:]) / 100
    except ValueError:
        return str(variant)
    return f"{index.upper()} > {threshold_value:.2f}"


def threshold_holdout_rows(frame):
    """Summarize selected thresholds against both baselines on shared dates."""
    rows = []
    holdout = frame[frame["split"].astype(str).str.lower() == "holdout"]
    for key, (name, capacity) in RESERVOIRS.items():
        lake = holdout[holdout["key"] == key].copy()
        if lake.empty:
            continue
        selected_values = lake["selected_from_early_dates"].dropna().astype(str)
        if selected_values.empty:
            continue
        selected = selected_values.iloc[0]
        selected_column = f"{selected}_prediction_mcft"
        if selected_column not in lake.columns:
            continue
        same_scale = "mndwi_p000" if key == "chembarambakkam" else "ndwi_p000"
        same_scale_column = f"{same_scale}_prediction_mcft"
        current_column = "current_baseline_prediction_mcft"
        if same_scale_column not in lake.columns:
            continue
        for column in (
            "official_storage_mcft", selected_column,
            same_scale_column, current_column,
        ):
            lake[column] = pd.to_numeric(lake[column], errors="coerce")
        lake = lake.dropna(subset=[
            "official_storage_mcft", selected_column,
            same_scale_column, current_column,
        ])
        if lake.empty:
            continue

        actual = lake["official_storage_mcft"]

        def rmse(column):
            return float(((lake[column] - actual) ** 2).mean() ** 0.5)

        selected_error = lake[selected_column] - actual

        selected_rmse = rmse(selected_column)
        same_scale_rmse = rmse(same_scale_column)
        current_rmse = rmse(current_column)
        best_baseline = min(same_scale_rmse, current_rmse)
        if selected_rmse < best_baseline:
            readout = "Selected method beats both baselines"
        elif selected_rmse <= best_baseline:
            readout = "Selected method matches best baseline"
        else:
            readout = "Baseline scores better"
        rows.append({
            "reservoir": key,
            "lake": name,
            "selected_method": describe_threshold_variant(selected),
            "holdout_pairs": len(lake),
            "selected_rmse_mcft": selected_rmse,
            "selected_rmse_pct_capacity": selected_rmse / capacity * 100,
            "selected_mae_mcft": float(selected_error.abs().mean()),
            "selected_bias_mcft": float(selected_error.mean()),
            "same_scale_baseline": describe_threshold_variant(same_scale),
            "same_scale_rmse_mcft": same_scale_rmse,
            "current_baseline_rmse_mcft": current_rmse,
            "low_storage_pairs": int((actual < 300.0).sum()),
            "readout": readout,
        })
    return pd.DataFrame(rows)


def latest_valid(df, col):
    d = df.dropna(subset=[col])
    return d.iloc[-1] if not d.empty else None


def current_reading(hist):
    sat = latest_valid(hist, "sat_estimate_mcft")
    off = latest_valid(hist, "official_mcft")
    if sat is None and off is None:
        return None
    if off is None:
        return sat["date"], sat["sat_estimate_mcft"], "Sentinel-2"
    if sat is None or off["date"] >= sat["date"]:
        return off["date"], off["official_mcft"], "CMWSSB"
    return sat["date"], sat["sat_estimate_mcft"], "Sentinel-2"


def assess(key, fc, threshold, now_pct=None):
    cap = RESERVOIRS[key][1]
    result = {"level": "unknown", "reason": None}
    if not fc.empty:
        last = fc.iloc[-1]
        yhat = min(max(last["yhat"], 0), cap)
        lo = min(max(last["yhat_lower"], 0), cap)
        hi = min(max(last["yhat_upper"], 0), cap)
        pct = yhat / cap * 100
        hi_pct = hi / cap * 100
        if hi_pct < threshold:
            level = "critical"
        elif pct < threshold:
            level = "watch"
        else:
            level = "ok"
        result.update(
            {
                "level": level,
                "reason": "forecast",
                "date": last["ds"],
                "yhat": yhat,
                "lo": lo,
                "hi": hi,
                "pct": pct,
                "lo_pct": lo / cap * 100,
                "hi_pct": hi_pct,
            }
        )
    if now_pct is not None and now_pct < threshold:
        result["level"] = "critical"
        result["reason"] = "current"
    return result


def pill(level):
    label, color = STATUS[level]
    return f'<span class="pill" style="background:{color}">{label}</span>'


def overview_card(key, hist, fc, threshold):
    name, cap = RESERVOIRS[key]
    reading = current_reading(hist)
    now_value = reading[1] / cap * 100 if reading is not None else None
    a = assess(key, fc, threshold, now_value)
    color = STATUS[a["level"]][1]
    if reading is None:
        now_html = '<div class="pct">–</div><div class="sub">No readings yet</div>'
        now_pct = 0
    else:
        d, v, src = reading
        now_pct = min(max(v / cap * 100, 0), 100)
        now_html = f'<div class="pct">{now_pct:.0f}%</div><div class="sub">{v:.0f} of {cap} mcft · {src}, {d:%d %b %Y}</div>'
    tick = ""
    fc_html = '<div class="sub">No forecast available</div>'
    if "pct" in a:
        tick = f'<div class="tick" style="left:calc({min(a["pct"], 100):.1f}% - 1px)"></div>'
        fc_html = f'<div class="sub">Forecast for {a["date"]:%d %b}: {a["pct"]:.0f}% ({a["lo_pct"]:.0f}–{a["hi_pct"]:.0f}%)</div>'
    html = (
        f'<div class="card"><h4>{name}{pill(a["level"])}</h4>{now_html}'
        f'<div class="tank"><div class="fill" style="width:{now_pct:.1f}%;background:{color}"></div>{tick}</div>'
        f"{fc_html}</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def style_fig(fig, height=380):
    title_margin = fig.layout.margin.t if fig.layout.margin and fig.layout.margin.t else 0
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=max(30, title_margin), b=10),
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Public Sans, sans-serif", color="#0E3B47"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#E3EEF1")
    fig.update_yaxes(gridcolor="#E3EEF1")
    if not (fig.layout.yaxis.title and fig.layout.yaxis.title.text):
        fig.update_yaxes(title="Storage (mcft)")
    return fig


def trend_chart(hist, start, end, cap):
    d = hist[(hist["date"] >= start) & (hist["date"] <= end)]
    fig = go.Figure()
    sat = d.dropna(subset=["sat_estimate_mcft"])
    off = d.dropna(subset=["official_mcft"])
    fig.add_trace(go.Scatter(x=sat["date"], y=sat["sat_estimate_mcft"], mode="lines+markers", name="Sentinel-2 estimate", line=dict(color=SAT_COLOR, width=2), marker=dict(size=5)))
    fig.add_trace(go.Scatter(x=off["date"], y=off["official_mcft"], mode="markers", name="CMWSSB official", marker=dict(color=OFFICIAL_COLOR, size=7, symbol="diamond")))
    fig.add_hline(y=cap, line_dash="dot", line_color="#7A8B91", annotation_text="Full capacity", annotation_position="top left")
    return style_fig(fig)


def forecast_chart(hist, fc, cap, threshold):
    fc = fc.copy()
    model = str(fc["model"].iloc[0]) if "model" in fc and not fc.empty else ""
    is_xgboost = model.startswith("xgboost_satellite")
    for c in ("yhat", "yhat_lower", "yhat_upper"):
        fc[c] = fc[c].clip(lower=0, upper=cap)
    start = fc["ds"].min() - pd.to_timedelta(120, unit="D")
    h = hist[hist["date"] >= start]
    fig = go.Figure()
    sat = h.dropna(subset=["sat_estimate_mcft"])
    off = h.dropna(subset=["official_mcft"])
    fig.add_trace(go.Scatter(x=sat["date"], y=sat["sat_estimate_mcft"], mode="lines+markers", name="Sentinel-2 estimate", line=dict(color=SAT_COLOR, width=2), marker=dict(size=5)))
    fig.add_trace(go.Scatter(x=off["date"], y=off["official_mcft"], mode="markers", name="CMWSSB official", marker=dict(color=OFFICIAL_COLOR, size=7, symbol="diamond")))
    fig.add_trace(go.Scatter(x=fc["ds"], y=fc["yhat_upper"], mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    band_name = "Approx. backtest error range" if is_xgboost else "Historical-change range"
    forecast_name = "XGBoost path to day-15 estimate" if is_xgboost else "Forecast"
    fig.add_trace(go.Scatter(x=fc["ds"], y=fc["yhat_lower"], mode="lines", line=dict(width=0), fill="tonexty", fillcolor=BAND_COLOR, name=band_name, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=fc["ds"], y=fc["yhat"], mode="lines+markers", name=forecast_name, line=dict(color=FORECAST_COLOR, width=2, dash="dash"), marker=dict(size=5)))
    fig.add_hline(y=threshold / 100 * cap, line_dash="dash", line_color="#B3372E", annotation_text=f"Alert threshold ({threshold:.0f}%)", annotation_position="bottom left")
    return style_fig(fig)


def forecast_table(fc, cap):
    t = fc.copy()
    for c in ("yhat", "yhat_lower", "yhat_upper"):
        t[c] = t[c].clip(lower=0, upper=cap)
    out = pd.DataFrame(
        {
            "Date": t["ds"].dt.strftime("%d %b %Y"),
            "Forecast (mcft)": t["yhat"].round(1),
            "Lower (mcft)": t["yhat_lower"].round(1),
            "Upper (mcft)": t["yhat_upper"].round(1),
            "% of capacity": (t["yhat"] / cap * 100).round(1),
        }
    )
    return out


core_data_error = None
try:
    core_data = load_dashboard_from_api(API_BASE_URL)
    H = pd.DataFrame(core_data["history"])
    H["date"] = pd.to_datetime(H["date"], errors="coerce")
    H["key"] = H["reservoir"].map(canon)

    F = pd.DataFrame(core_data["forecasts"])
    if not F.empty:
        F["ds"] = pd.to_datetime(F["ds"], errors="coerce")
        F = F[F["ds"] >= pd.Timestamp.today().normalize()]
        F["key"] = F["reservoir"].map(canon)
    else:
        F = pd.DataFrame(columns=["ds", "reservoir", "key", "yhat", "yhat_lower", "yhat_upper"])
    M = core_data["metrics"]
    data_status = core_data["data_status"]
    data_source_label = f"FastAPI + {data_status.get('database', 'database')}"
except (requests.RequestException, ValueError, KeyError, TypeError) as e:
    core_data_error = str(e)
    try:
        H = load_history(os.path.getmtime("dashboard_data.csv"))
        F = load_forecast(os.path.getmtime("reservoir_forecasts.csv"))
    except FileNotFoundError as missing_file:
        st.error(
            f"AquaSat API is unavailable and a required CSV file is missing: {missing_file.filename}. "
            "Start the local API or restore dashboard_data.csv and reservoir_forecasts.csv."
        )
        st.stop()
    M = load_metrics(os.path.getmtime("metrics.csv") if os.path.exists("metrics.csv") else None)
    data_source_label = "CSV compatibility mode"
    data_status = {}

threshold_data_error = None
try:
    T = load_threshold_review()
except (OSError, ValueError, pd.errors.ParserError) as e:
    T = pd.DataFrame()
    threshold_data_error = str(e)
s1_data_error = None
try:
    S1 = load_sentinel1_review()
except (OSError, ValueError, pd.errors.ParserError) as e:
    S1 = pd.DataFrame()
    s1_data_error = str(e)
rainfall_data_error = None
rainfall_history_file = (
    "rainfall_reservoir_daily_2016_2024.csv"
    if os.path.exists("rainfall_reservoir_daily_2016_2024.csv")
    else "rainfall_reservoir_daily_2016_2019.csv"
)
try:
    RAIN = load_rainfall_history(
        rainfall_history_file,
        os.path.getmtime(rainfall_history_file),
    )
except (OSError, ValueError, pd.errors.ParserError) as e:
    RAIN = pd.DataFrame()
    rainfall_data_error = str(e)
rainfall_backtest_error = None
try:
    RAIN_SUMMARY = load_rainfall_backtest(
        os.path.getmtime("rainfall_2019_crisis_summary.csv")
    )
except (OSError, ValueError, pd.errors.ParserError) as e:
    RAIN_SUMMARY = pd.DataFrame()
    rainfall_backtest_error = str(e)
rainfall_predictions_error = None
try:
    RAIN_PREDICTIONS = load_rainfall_predictions(
        os.path.getmtime("rainfall_2019_crisis_predictions.csv")
    )
except (OSError, ValueError, pd.errors.ParserError) as e:
    RAIN_PREDICTIONS = pd.DataFrame()
    rainfall_predictions_error = str(e)
multiyear_rainfall_error = None
try:
    RAIN_MULTIYEAR_SUMMARY = load_rainfall_multiyear_summary(
        os.path.getmtime("rainfall_2019_2024_summary_candidate.csv")
    )
except (OSError, ValueError, pd.errors.ParserError) as e:
    RAIN_MULTIYEAR_SUMMARY = pd.DataFrame()
    multiyear_rainfall_error = str(e)
yearly_rainfall_error = None
try:
    RAIN_YEARLY_SUMMARY = load_rainfall_yearly_summary(
        os.path.getmtime("rainfall_2019_2024_by_year_candidate.csv")
    )
except (OSError, ValueError, pd.errors.ParserError) as e:
    RAIN_YEARLY_SUMMARY = pd.DataFrame()
    yearly_rainfall_error = str(e)

st.session_state.setdefault("threshold", 20.0)

with st.sidebar:
    st.title("AquaSat")
    st.caption("Satellite-based storage estimates for Chennai's reservoirs")
    if data_source_label.startswith("FastAPI +"):
        st.caption(f"Data service: FastAPI · {data_status.get('database', 'database')}")
        if data_status.get("last_synced_at_utc"):
            st.caption(f"Database synced: {data_status['last_synced_at_utc']}")
        if st.button("Refresh from current CSV files", width="stretch"):
            load_dashboard_from_api.clear()
            st.rerun()
    else:
        st.warning("API unavailable; reading dashboard history and forecast from CSV files.")
        if core_data_error:
            st.caption(f"API detail: {core_data_error[:180]}")
    reservoir_keys = list(RESERVOIRS)
    selected_reservoir = st.selectbox(
        "Reservoir",
        options=range(len(reservoir_keys)),
        format_func=lambda index: RESERVOIRS[reservoir_keys[index]][0],
        key="reservoir_selector_index",
    )
    key = reservoir_keys[int(selected_reservoir)]
    st.metric("Alert threshold", f"{st.session_state['threshold']:.0f}% of capacity")
    with st.expander("Admin"):
        pw = st.text_input("Password", type="password")
        try:
            secret = st.secrets.get("ADMIN_PASSWORD", None)
        except Exception:
            secret = None
        if secret and pw == secret:
            st.session_state["threshold"] = st.slider("Alert threshold (% of capacity)", 5, 50, int(st.session_state["threshold"]))
        elif pw:
            st.caption("Incorrect password.")
        elif not secret:
            st.caption("Set ADMIN_PASSWORD in .streamlit/secrets.toml to enable threshold changes.")

threshold = st.session_state["threshold"]

st.title("Chennai reservoir storage")
st.caption("Satellite estimates are advisory. Cross-check with official CMWSSB figures before making any decision.")

last_dates = [d for d in (H["date"].max(),) if pd.notna(d)]
if last_dates:
    age = (pd.Timestamp.today().normalize() - last_dates[0]).days
    if age >= STALE_DAYS:
        st.warning(f"Latest reading in the data is from {last_dates[0]:%d %b %Y} ({age} days ago). Re-run extraction and forecasting to refresh.")
if F.empty:
    st.warning("No unexpired forecast rows are available. The stored forecast file has no future dates; regenerate it before using forecast alerts.")

tab_overview, tab_detail, tab_method, tab_satellite, tab_rainfall = st.tabs(
    ["All reservoirs", "Reservoir detail", "Methodology", "Satellite validation", "Rainfall validation"]
)

with tab_overview:
    keys = list(RESERVOIRS.keys())
    cols = st.columns(len(keys))
    for col, k in zip(cols, keys):
        with col:
            overview_card(k, H[H["key"] == k], F[F["key"] == k], threshold)
    st.caption("The dark tick on each bar marks the forecast level at the end of the forecast window. Colour shows alert status against the threshold.")

with tab_detail:
    name, cap = RESERVOIRS[key]
    hist = H[H["key"] == key]
    fc = F[F["key"] == key]
    reading = current_reading(hist)
    now_value = reading[1] / cap * 100 if reading is not None else None
    a = assess(key, fc, threshold, now_value)
    m = M.get(key, {})

    st.header(name)

    if a["reason"] == "current":
        r_date, r_value, r_src = reading
        st.error(f"Latest reading ({r_src}, {r_date:%d %b %Y}) is {r_value:.0f} mcft ({r_value / cap * 100:.0f}% of capacity), already below the {threshold:.0f}% threshold.")
    elif a["level"] == "critical":
        st.error(f"Forecast storage on {a['date']:%d %b %Y} is {a['yhat']:.0f} mcft ({a['pct']:.0f}% of capacity), range {a['lo']:.0f}–{a['hi']:.0f} mcft. Even the upper bound is below the {threshold:.0f}% threshold.")
    elif a["level"] == "watch":
        rmse = m.get("rmse")
        extra = f" Calibration error for this reservoir is about ±{rmse:.0f} mcft, so this may be noise." if rmse else ""
        st.warning(f"Forecast storage on {a['date']:%d %b %Y} is {a['yhat']:.0f} mcft ({a['pct']:.0f}% of capacity), below the {threshold:.0f}% threshold. The upper bound ({a['hi']:.0f} mcft) is above it.{extra}")
    elif a["level"] == "ok":
        st.success(f"Forecast storage on {a['date']:%d %b %Y} is {a['yhat']:.0f} mcft ({a['pct']:.0f}% of capacity), above the {threshold:.0f}% threshold.")
    else:
        st.info("No forecast available for this reservoir.")

    sat = latest_valid(hist, "sat_estimate_mcft")
    off = latest_valid(hist, "official_mcft")
    both = hist.dropna(subset=["sat_estimate_mcft", "official_mcft"])

    if sat is not None and off is not None and sat["date"] < off["date"]:
        gap_days = (off["date"] - sat["date"]).days
        st.warning(
            f"The stored satellite series ends on {sat['date']:%d %b %Y}, "
            f"{gap_days} days before the latest official reading on "
            f"{off['date']:%d %b %Y}. Satellite estimates exist only on usable "
            "image dates; leave the days between observations unfilled."
        )

    c1, c2, c3 = st.columns(3)
    with c1:
        if sat is not None:
            sat_value = sat["sat_estimate_mcft"]
            sat_limit = m.get("valid_up_to")
            sat_text = f"≥ {sat_limit:.0f} mcft" if sat_limit and sat_value >= sat_limit - 0.5 else f"{sat_value:.0f} mcft"
            st.metric(f"Satellite estimate ({sat['date']:%d %b %Y})", sat_text, f"{sat_value / cap * 100:.0f}% of capacity", delta_color="off")
        else:
            st.metric("Satellite estimate", "n/a")
    with c2:
        if off is not None:
            st.metric(f"CMWSSB official ({off['date']:%d %b %Y})", f"{off['official_mcft']:.0f} mcft", f"{off['official_mcft'] / cap * 100:.0f}% of capacity", delta_color="off")
        else:
            st.metric("CMWSSB official", "n/a")
    with c3:
        if not both.empty:
            r = both.iloc[-1]
            st.metric(f"Satellite minus official ({r['date']:%d %b %Y})", f"{r['sat_estimate_mcft'] - r['official_mcft']:+.0f} mcft")
        else:
            st.metric("Satellite minus official", "n/a")

    st.subheader("Historical trend")
    if hist.empty:
        st.info("No historical data for this reservoir.")
    else:
        dmin, dmax = hist["date"].min().date(), hist["date"].max().date()
        picked = st.date_input("Date range", value=(dmin, dmax), min_value=dmin, max_value=dmax, key=f"range_{key}")
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            start, end = pd.Timestamp(picked[0]), pd.Timestamp(picked[1])
        else:
            start, end = pd.Timestamp(dmin), pd.Timestamp(dmax)
        st.plotly_chart(trend_chart(hist, start, end, cap), width="stretch")

    st.subheader("Forecast")
    if fc.empty:
        st.info("No forecast available for this reservoir.")
    else:
        st.plotly_chart(forecast_chart(hist, fc, cap, threshold), width="stretch")
        model = str(fc["model"].iloc[0]) if "model" in fc and not fc.empty else ""
        if model.startswith("xgboost_satellite"):
            final = fc.sort_values("ds").iloc[-1]
            source = str(final.get("satellite_source", "satellite history"))
            feature_date = str(final.get("satellite_feature_date", ""))
            feature_age = final.get("satellite_age_days", "")
            feature_note = (
                f" Latest available satellite input: {source} on {feature_date} "
                f"({feature_age} days from forecast origin)."
                if feature_date and pd.notna(feature_age) and str(feature_age).strip()
                else f" Latest available satellite input: {source}."
            )
            st.caption(
                "Live method: 15-day XGBoost forecast of official CMWSSB storage, "
                "using official history plus the latest calibrated satellite "
                "storage estimate. The satellite history prefers Sentinel-2 and fills "
                "same-lake/date gaps with Sentinel-1."
                + feature_note
                + " The satellite input is available at or before the forecast "
                "origin. The day-15 endpoint is model-generated; the daily line between "
                "the latest official reading and that endpoint is interpolated. The "
                "shaded range comes from rolling-origin backtest error quantiles, is "
                "approximate, and is not a calibrated probability interval. This "
                "feature did not show a broad forecast-accuracy gain in the backtest."
            )
            if pd.notna(final.get("backtest_rmse_mcft")) and pd.notna(final.get("backtest_pairs")):
                st.caption(
                    f"15-day forecast backtest RMSE: {float(final['backtest_rmse_mcft']):.1f} mcft "
                    f"across {int(final['backtest_pairs'])} pairs. This is forecast error, "
                    "separate from the satellite calibration RMSE shown below."
                )
        else:
            st.caption(
                "This is the seasonal/damped-trend baseline forecast. The shaded "
                "range comes from historical monthly changes and is not a calibrated "
                "probability interval."
            )
        st.dataframe(forecast_table(fc, cap), width="stretch", hide_index=True)

    st.subheader("Accuracy of the satellite estimate")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Calibration model", m.get("model") or "n/a")
    rmse = m.get("rmse")
    a2.metric("RMSE", f"{rmse:.0f} mcft" if rmse else "n/a")
    a3.metric("RMSE as % of capacity", f"{rmse / cap * 100:.0f}%" if rmse else "n/a")
    bias = m.get("bias")
    a4.metric("Bias", f"{bias:+.0f} mcft" if bias is not None else "n/a")
    cv_method = m.get("cv_method")
    if cv_method == "expanding-window chronological":
        st.caption("Calibration metrics use expanding-window chronological validation on exact-date satellite and CMWSSB matches. They measure satellite storage-estimation error, not future forecast accuracy.")
    elif cv_method == "later 20% chronological holdout":
        st.caption("The water-area rule was selected on earlier dates; these scores use only the later 20% holdout. Storage calibration uses earlier exact-date CMWSSB matches. This measures satellite storage-estimation error, not future forecast accuracy.")
    elif cv_method:
        st.caption(f"Validation method recorded in metrics.csv: {cv_method}.")
    else:
        st.warning("This metrics.csv does not record its validation method. Rebuild it from a prior-only satellite candidate with build_clear95_dashboard.py before treating these accuracy scores as time-forward validation.")
    if m.get("n_matched"):
        st.caption(f"Scored on {int(m['n_matched'])} exact-date pairs using only earlier CMWSSB matches.")
    if m.get("validation_source"):
        st.caption(f"Source and quality gate: {m['validation_source']}.")
    if m.get("low_storage_pairs") is not None:
        low_count = int(m["low_storage_pairs"])
        if low_count and m.get("low_storage_rmse") is not None:
            st.caption(f"Below 300 mcft: RMSE {m['low_storage_rmse']:.0f} mcft across {low_count} exact-date holdout pairs.")
        else:
            st.info("No below-300 mcft holdout pairs are available; drought-range accuracy is unverified.")
    if m.get("valid_up_to"):
        st.caption(f"For this reservoir the water area stops growing above about {m['valid_up_to']:.0f} mcft, so satellite estimates are capped there and the accuracy figures cover storage up to that level only.")

with tab_method:
    st.header("How this works")
    st.markdown(
        """
**Water area from satellite.** Sentinel-2 is the optical water-area source. The active v3 pipeline uses clear95 scenes and the NDWI/MNDWI threshold selected from earlier exact-date CMWSSB matches for each lake: Poondi MNDWI > 0.10, Cholavaram MNDWI > 0.10, Puzhal MNDWI > −0.10, Chembarambakkam MNDWI > −0.10, and Veeranam MNDWI > 0.00. The Satellite validation tab reports the later-date holdout. Estimates remain advisory; the current holdout has few or no low-storage pairs for several lakes.

**Sentinel-1 fallback.** Sentinel-1 is radar, not an NDWI/MNDWI source. Its VV/VH backscatter is thresholded into candidate water areas, then calibrated separately against earlier exact-date CMWSSB readings. The combined satellite history prefers Sentinel-2 and uses Sentinel-1 only when no usable S2 estimate exists on that same lake/date. This history is a lagged feature for the live XGBoost forecast; it does not change the dashboard's satellite storage cards or supply raw pixels to the forecast. S1 holdout RMSE ranges from 59 to 616 mcft across lakes, with 428–616 mcft errors for four lakes. The forecast backtest showed no broad improvement from adding the S1 gap-fill, so the forecast remains advisory and is not presented as more accurate.

**Area to storage.** The active satellite series uses an early-selected Sentinel-2 water-area rule and isotonic regression. Each storage estimate is calibrated using only earlier exact-date CMWSSB matches. Accuracy reports the later 20% chronological holdout, which kept area-rule selection separate from scoring. Gaussian Process and power-law fits in `prepare_data.py` are comparison candidates, not the active calibrator. None of these area-to-storage methods is the future-storage forecast.

**Forecast.** The live dashboard uses a 15-day XGBoost model to predict future official CMWSSB storage. It uses official-storage history and, when available, the latest calibrated satellite-storage estimate available at the forecast origin as a lag feature. The combined satellite history prefers Sentinel-2 and fills missing same-lake/date estimates with Sentinel-1. XGBoost receives storage values, not raw imagery, MNDWI, or VV/VH pixels. The day-15 endpoint is model-generated; the daily display path is interpolated. The shaded range is based on rolling-origin backtest errors and is an approximate historical range, not a calibrated probability interval. The backtest showed mixed results and no broad accuracy gain from the S1 fallback.

**Rainfall validation.** A 2019 crisis-window study and a separate 2019–2024 backtest compare XGBoost with and without antecedent 7-, 30-, and 90-day rainfall totals. The longer run has 344 exact-date forecast pairs; pooled RMSE is slightly higher with rainfall, with mixed results by year and reservoir. Rainfall remains research-only. The daily IMD 0.25° series uses each reservoir's nearest grid cell, a local proxy rather than catchment-average rain, and excludes rain on or after the forecast origin.

**Alerts.** An alert is shown as Low when the latest reading is already below the threshold or the whole forecast range is below it, and as Watch when only the central forecast is below it.

**Limits.** Calibration error is large for some reservoirs, so treat small differences from official figures as noise. Satellite estimates are advisory and do not replace official CMWSSB figures.
"""
    )

with tab_rainfall:
    st.header("Rainfall history and forecast validation")
    st.caption(
        "The chart shows the 2019 rainfall case study. The backtest below scores "
        "15-day forecast targets across 2019–2024 using exact CMWSSB readings."
    )
    st.info(
        "This is a research comparison only. Rainfall features use only rain observed "
        "strictly before each forecast origin; they do not feed the live forecast."
    )

    if rainfall_data_error:
        st.warning(
            "The rainfall history is unavailable. Build a daily IMD rainfall CSV "
            "with build_imd_rainfall.py. "
            f"Details: {rainfall_data_error}"
        )
    else:
        selected_rain = RAIN[
            (RAIN["key"] == key)
            & RAIN["date"].between(pd.Timestamp("2019-01-01"), pd.Timestamp("2019-08-31"))
        ].sort_values("date")
        official_2019 = pd.DataFrame(columns=["target_date", "actual_mcft"])
        if not RAIN_PREDICTIONS.empty:
            official_2019 = RAIN_PREDICTIONS[
                (RAIN_PREDICTIONS["key"] == key)
                & RAIN_PREDICTIONS["target_date"].between(
                    pd.Timestamp("2019-01-01"), pd.Timestamp("2019-08-31")
                )
            ].dropna(subset=["actual_mcft"]).sort_values("target_date")
        if not selected_rain.empty:
            chart = go.Figure()
            chart.add_trace(go.Bar(
                x=selected_rain["date"], y=selected_rain["rainfall_mm"],
                name="IMD daily rainfall", marker_color="rgba(27,108,168,0.42)",
                yaxis="y2",
            ))
            if not official_2019.empty:
                chart.add_trace(go.Scatter(
                    x=official_2019["target_date"], y=official_2019["actual_mcft"],
                    mode="markers", name="CMWSSB official · scored targets",
                    marker=dict(size=6, color=OFFICIAL_COLOR),
                    yaxis="y",
                ))
            chart.update_layout(
                title=f"Daily rainfall and official storage · {RESERVOIRS[key][0]} · 2019",
                xaxis=dict(title="Date"),
                yaxis=dict(title="Official storage (mcft)", rangemode="tozero"),
                yaxis2=dict(
                    title="Daily rainfall (mm)", overlaying="y", side="right",
                    showgrid=False, rangemode="tozero",
                ),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                margin=dict(t=80),
            )
            st.plotly_chart(style_fig(chart, height=430), width="stretch")
            grid_offset = selected_rain["grid_offset_km"].dropna()
            if not grid_offset.empty:
                st.caption(
                    f"Rainfall uses the nearest 0.25° IMD grid cell to the reservoir "
                    f"centre (about {grid_offset.iloc[0]:.1f} km from the representative "
                    "centre coordinate). This is a local proxy, not catchment-average rainfall."
                )
            if not official_2019.empty:
                st.caption(
                    "Orange markers are only the exact CMWSSB readings used in the "
                    "time-forward forecast comparison; disputed lake/date labels are excluded."
                )
        else:
            st.info("No daily IMD rainfall rows are available for this reservoir and period.")
    if rainfall_predictions_error:
        st.caption(f"Scored CMWSSB target markers are unavailable: {rainfall_predictions_error}")

    if rainfall_backtest_error:
        st.warning(
            "The rainfall backtest summary is unavailable. Run "
            "backtest_2019_rainfall.py after building the rainfall series. "
            f"Details: {rainfall_backtest_error}"
        )
    elif not RAIN_SUMMARY.empty:
        pooled = RAIN_SUMMARY[
            RAIN_SUMMARY["reservoir"].astype(str).str.lower() == "all reservoirs pooled"
        ]
        lake_summary = RAIN_SUMMARY[
            RAIN_SUMMARY["reservoir"].astype(str).str.lower() != "all reservoirs pooled"
        ].copy()
        if not pooled.empty:
            overall = pooled.iloc[0]
            cards = st.columns(4)
            cards[0].metric("Comparable forecast pairs", f"{int(overall['pairs'])}")
            cards[1].metric("Baseline XGBoost RMSE", f"{overall['baseline_rmse_mcft']:.1f} mcft")
            cards[2].metric(
                "With rainfall RMSE", f"{overall['rainfall_rmse_mcft']:.1f} mcft",
                delta=f"{overall['rmse_change_mcft']:+.1f} mcft vs baseline",
                delta_color="inverse",
            )
            cards[3].metric(
                "Capacity-normalized RMSE",
                f"{overall['rainfall_rmse_pct_capacity']:.2f}%",
                delta=f"{overall['rmse_change_pct_capacity']:+.2f} pp vs baseline",
                delta_color="inverse",
            )
        if not lake_summary.empty:
            lake_summary["Reservoir"] = lake_summary["reservoir"].map(
                lambda lake: RESERVOIRS.get(lake, (lake.title(), 0))[0]
            )
            display = lake_summary[[
                "Reservoir", "pairs", "baseline_rmse_mcft", "rainfall_rmse_mcft",
                "rmse_change_mcft",
            ]].rename(columns={
                "pairs": "Forecast pairs",
                "baseline_rmse_mcft": "Baseline RMSE (mcft)",
                "rainfall_rmse_mcft": "With rainfall RMSE (mcft)",
                "rmse_change_mcft": "RMSE change (mcft)",
            })
            st.subheader("Forecast comparison by reservoir")
            st.dataframe(display.round(1), width="stretch", hide_index=True)

            detail = lake_summary[[
                "Reservoir", "baseline_rmse_pct_capacity",
                "rainfall_rmse_pct_capacity", "rmse_change_pct_capacity",
                "below_300_pairs", "baseline_below_300_rmse_mcft",
                "rainfall_below_300_rmse_mcft",
            ]].rename(columns={
                "baseline_rmse_pct_capacity": "Baseline RMSE (% capacity)",
                "rainfall_rmse_pct_capacity": "With rainfall RMSE (% capacity)",
                "rmse_change_pct_capacity": "Change (percentage points)",
                "below_300_pairs": "Pairs below 300 mcft",
                "baseline_below_300_rmse_mcft": "Baseline low-storage RMSE",
                "rainfall_below_300_rmse_mcft": "Rainfall low-storage RMSE",
            })
            with st.expander("Capacity-normalized and low-storage details"):
                st.caption(
                    "Low-storage scores use only exact-date forecast pairs below 300 mcft; "
                    "small pair counts make them descriptive rather than conclusive."
                )
                st.dataframe(detail.round(1), width="stretch", hide_index=True)
        st.caption(
            "Rainfall features are antecedent totals over 7, 30, and 90 days. A feature "
            "is omitted when its full daily window is not available. Lower RMSE is better; "
            "this single 2019 window does not establish that rainfall improves live forecasts."
        )
    else:
        st.info("The 2019 XGBoost rainfall comparison is not available yet.")

    st.divider()
    st.subheader("Multi-year comparison · 2019–2024")
    st.caption(
        "Rainfall history covers 2016–2024. The 2016–2018 observations provide earlier "
        "training and antecedent-rain history; forecast targets are scored only from 2019 onward."
    )
    if multiyear_rainfall_error:
        st.info(
            "Multi-year results are unavailable. Run the 2019–2024 backtest to create "
            "rainfall_2019_2024_summary_candidate.csv. "
            f"Details: {multiyear_rainfall_error}"
        )
    else:
        pooled_multi = RAIN_MULTIYEAR_SUMMARY[
            RAIN_MULTIYEAR_SUMMARY["reservoir"].astype(str).str.lower()
            == "all reservoirs pooled"
        ]
        lake_multi = RAIN_MULTIYEAR_SUMMARY[
            RAIN_MULTIYEAR_SUMMARY["reservoir"].astype(str).str.lower()
            != "all reservoirs pooled"
        ].copy()
        if not pooled_multi.empty:
            overall_multi = pooled_multi.iloc[0]
            cards = st.columns(4)
            cards[0].metric("Comparable forecast pairs", f"{int(overall_multi['pairs'])}")
            cards[1].metric(
                "Baseline XGBoost RMSE", f"{overall_multi['baseline_rmse_mcft']:.1f} mcft"
            )
            cards[2].metric(
                "With rainfall RMSE", f"{overall_multi['rainfall_rmse_mcft']:.1f} mcft",
                delta=f"{overall_multi['rmse_change_mcft']:+.1f} mcft vs baseline",
                delta_color="inverse",
            )
            cards[3].metric(
                "Capacity-normalized RMSE",
                f"{overall_multi['rainfall_rmse_pct_capacity']:.2f}%",
                delta=f"{overall_multi['rmse_change_pct_capacity']:+.2f} pp vs baseline",
                delta_color="inverse",
            )
            st.warning(
                "Rainfall did not improve the pooled 2019–2024 result and the scores vary "
                "by year and reservoir. It remains outside the live forecast."
            )
        else:
            st.info("The multi-year summary has no pooled score row.")

        if not lake_multi.empty:
            lake_multi["Reservoir"] = lake_multi["reservoir"].map(
                lambda lake: RESERVOIRS.get(lake, (str(lake).title(), 0))[0]
            )
            lake_display = lake_multi[[
                "Reservoir", "pairs", "baseline_rmse_mcft", "rainfall_rmse_mcft",
                "rmse_change_mcft",
            ]].rename(columns={
                "pairs": "Forecast pairs",
                "baseline_rmse_mcft": "Baseline RMSE (mcft)",
                "rainfall_rmse_mcft": "With rainfall RMSE (mcft)",
                "rmse_change_mcft": "Change (mcft; lower is better)",
            })
            st.dataframe(
                lake_display.round(1), width="stretch", hide_index=True,
                key="rainfall_multi_lake_summary",
            )

            lake_details = lake_multi[[
                "Reservoir", "baseline_rmse_pct_capacity",
                "rainfall_rmse_pct_capacity", "rmse_change_pct_capacity",
                "below_300_pairs", "baseline_below_300_rmse_mcft",
                "rainfall_below_300_rmse_mcft",
            ]].rename(columns={
                "baseline_rmse_pct_capacity": "Baseline RMSE (% capacity)",
                "rainfall_rmse_pct_capacity": "Rainfall RMSE (% capacity)",
                "rmse_change_pct_capacity": "Change (percentage points)",
                "below_300_pairs": "Pairs below 300 mcft",
                "baseline_below_300_rmse_mcft": "Baseline low-storage RMSE",
                "rainfall_below_300_rmse_mcft": "Rainfall low-storage RMSE",
            })
            with st.expander("Multi-year capacity-normalized and low-storage details"):
                st.caption(
                    "Low-storage scores have uneven pair counts across lakes; interpret them "
                    "as exploratory, especially where only a few pairs are available."
                )
                st.dataframe(
                    lake_details.round(1), width="stretch", hide_index=True,
                    key="rainfall_multi_lake_details",
                )

        if yearly_rainfall_error:
            st.caption(f"Year-by-year scores are unavailable: {yearly_rainfall_error}")
        elif not RAIN_YEARLY_SUMMARY.empty:
            pooled_yearly = RAIN_YEARLY_SUMMARY[
                RAIN_YEARLY_SUMMARY["reservoir"].astype(str).str.lower()
                == "all reservoirs pooled"
            ].sort_values("target_year")
            if not pooled_yearly.empty:
                year_chart = go.Figure()
                year_chart.add_trace(go.Scatter(
                    x=pooled_yearly["target_year"],
                    y=pooled_yearly["baseline_rmse_mcft"],
                    mode="lines+markers", name="Baseline XGBoost",
                    line=dict(color="#7A8B91"),
                ))
                year_chart.add_trace(go.Scatter(
                    x=pooled_yearly["target_year"],
                    y=pooled_yearly["rainfall_rmse_mcft"],
                    mode="lines+markers", name="XGBoost with rainfall",
                    line=dict(color=SAT_COLOR),
                ))
                year_chart.update_layout(
                    title="Pooled RMSE by target year · lower is better",
                    xaxis=dict(title="Target year", tickmode="linear", dtick=1),
                    yaxis=dict(title="RMSE (mcft)", rangemode="tozero"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                    margin=dict(t=80),
                )
                st.plotly_chart(
                    style_fig(year_chart, height=360), width="stretch",
                    key="rainfall_multi_year_chart",
                )

            with st.expander("Year-by-year scores by reservoir"):
                yearly_display = RAIN_YEARLY_SUMMARY.copy()
                yearly_display["Year"] = yearly_display["target_year"].astype(int)
                yearly_display["Reservoir"] = yearly_display["reservoir"].map(
                    lambda lake: (
                        "All reservoirs pooled"
                        if str(lake).strip().lower() == "all reservoirs pooled"
                        else RESERVOIRS.get(lake, (str(lake).title(), 0))[0]
                    )
                )
                yearly_display = yearly_display[[
                    "Year", "Reservoir", "pairs", "baseline_rmse_mcft",
                    "rainfall_rmse_mcft", "rmse_change_mcft",
                ]].rename(columns={
                    "pairs": "Forecast pairs",
                    "baseline_rmse_mcft": "Baseline RMSE (mcft)",
                    "rainfall_rmse_mcft": "With rainfall RMSE (mcft)",
                    "rmse_change_mcft": "Change (mcft)",
                })
                st.dataframe(
                    yearly_display.round(1), width="stretch", hide_index=True,
                    key="rainfall_multi_year_detail",
                )

    st.markdown(
        "Source: [India Meteorological Department daily 0.25° rainfall archive]"
        "(https://imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html). "
        "Rainfall from one nearest grid cell is not a watershed or catchment average, "
        "and rainfall alone does not measure reservoir inflow."
    )

with tab_satellite:
    st.header("Sentinel-2 threshold validation")
    st.caption(
        "This later-date holdout shows how the early-selected water-area rules performed. "
        "The selected Sentinel-2 rules feed the satellite history used by the forecast "
        "as a lagged storage feature; the forecast target remains future official storage."
    )
    if threshold_data_error:
        st.warning(f"Could not read the threshold comparison file: {threshold_data_error}")
    elif T.empty:
        st.info(
            "Threshold comparison results are not available. Put "
            "sentinel2_v3_clear95_threshold_comparison_v2_candidate.csv "
            "beside app.py to show them here."
        )
    else:
        comparison = threshold_holdout_rows(T)
        if comparison.empty:
            st.info("No comparable later-holdout predictions were found in the results file.")
        else:
            st.markdown(
                "The method was chosen using earlier dates. This table scores it on "
                "later holdout dates against the current per-lake method and a "
                "20 m like-for-like baseline. Lower RMSE is better."
            )
            display = comparison[[
                "lake", "selected_method", "holdout_pairs", "selected_rmse_mcft",
                "selected_rmse_pct_capacity", "selected_mae_mcft",
                "selected_bias_mcft", "same_scale_baseline",
                "same_scale_rmse_mcft", "current_baseline_rmse_mcft",
                "low_storage_pairs", "readout",
            ]].rename(columns={
                "lake": "Reservoir",
                "selected_method": "Early-date choice",
                "holdout_pairs": "Holdout pairs",
                "selected_rmse_mcft": "Selected RMSE (mcft)",
                "selected_rmse_pct_capacity": "Selected RMSE (% capacity)",
                "selected_mae_mcft": "Selected MAE (mcft)",
                "selected_bias_mcft": "Selected bias (mcft)",
                "same_scale_baseline": "20 m baseline",
                "same_scale_rmse_mcft": "20 m baseline RMSE",
                "current_baseline_rmse_mcft": "Current baseline RMSE",
                "low_storage_pairs": "Pairs below 300 mcft",
                "readout": "Holdout result",
            })
            for column in (
                "Selected RMSE (mcft)", "Selected RMSE (% capacity)",
                "Selected MAE (mcft)", "Selected bias (mcft)",
                "20 m baseline RMSE", "Current baseline RMSE",
            ):
                display[column] = display[column].round(1)
            st.dataframe(display, width="stretch", hide_index=True)

            puzhal_result = comparison[comparison["reservoir"] == "puzhal"]
            if not puzhal_result.empty:
                result = puzhal_result.iloc[0]
                st.warning(
                    f"Puzhal's early-date choice ({result['selected_method']}) scored "
                    f"{result['holdout_pairs']} later-date matches with RMSE "
                    f"{result['selected_rmse_mcft']:.0f} mcft and bias "
                    f"{result['selected_bias_mcft']:+.0f} mcft. There were "
                    f"{result['low_storage_pairs']} matches below 300 mcft. "
                    "Treat this as an experimental estimate, not a drought-validated "
                    "storage reading."
                )

            lake = T[
                (T["key"] == key) &
                (T["split"].astype(str).str.lower() == "holdout")
            ].copy()
            if lake.empty:
                st.info("No holdout chart is available for the selected reservoir.")
            else:
                selected = str(lake["selected_from_early_dates"].dropna().iloc[0])
                selected_column = f"{selected}_prediction_mcft"
                same_scale = "mndwi_p000" if key == "chembarambakkam" else "ndwi_p000"
                same_scale_column = f"{same_scale}_prediction_mcft"
                chart_columns = [
                    "official_storage_mcft", selected_column,
                    same_scale_column, "current_baseline_prediction_mcft",
                ]
                if all(column in lake.columns for column in chart_columns):
                    for column in chart_columns:
                        lake[column] = pd.to_numeric(lake[column], errors="coerce")
                    lake = lake.dropna(subset=chart_columns).sort_values("date")
                    if not lake.empty:
                        chart = go.Figure()
                        chart.add_trace(go.Scatter(
                            x=lake["date"], y=lake["official_storage_mcft"],
                            mode="markers", name="CMWSSB official",
                            marker=dict(color=OFFICIAL_COLOR, size=8, symbol="diamond"),
                        ))
                        chart.add_trace(go.Scatter(
                            x=lake["date"], y=lake[selected_column],
                            mode="lines+markers", name=f"Selected: {describe_threshold_variant(selected)}",
                            line=dict(color=FORECAST_COLOR, width=2),
                        ))
                        chart.add_trace(go.Scatter(
                            x=lake["date"], y=lake[same_scale_column],
                            mode="lines+markers", name=f"20 m baseline: {describe_threshold_variant(same_scale)}",
                            line=dict(color=SAT_COLOR, width=2, dash="dot"),
                        ))
                        chart.add_trace(go.Scatter(
                            x=lake["date"], y=lake["current_baseline_prediction_mcft"],
                            mode="lines+markers", name="Current baseline",
                            line=dict(color="#7A8B91", width=2, dash="dash"),
                        ))
                        chart.add_hline(
                            y=RESERVOIRS[key][1], line_dash="dot",
                            line_color="#7A8B91", annotation_text="Full capacity",
                        )
                        chart.update_layout(title=f"Later holdout · {RESERVOIRS[key][0]}")
                        st.plotly_chart(style_fig(chart, height=440), width="stretch")
            st.caption(
                "The holdout is a limited recent-date sample, not proof of drought-period "
                "performance. Values below 300 mcft are counted in the table."
            )

    st.divider()
    st.header("Sentinel-1 SAR fallback validation")
    st.caption(
        "Sentinel-1 does not change the dashboard's satellite storage cards. Its "
        "calibrated estimate can enter the live XGBoost forecast only as a prior "
        "lagged feature when S2 is missing on that lake/date. Threshold selection uses "
        "early dates; this section scores the selected rule on later exact-date "
        "CMWSSB holdout rows. Forecast backtesting did not show a broad gain from S1."
    )
    if s1_data_error:
        st.warning(f"Could not read the Sentinel-1 candidate file: {s1_data_error}")
    elif S1.empty:
        st.info(
            "Sentinel-1 candidate results are not available yet. After running the "
            "local validation command, place "
            "sentinel1_v3_oos_fallback_candidate.csv beside app.py."
        )
    else:
        estimates = S1.dropna(subset=["satellite_estimate_mcft"]).copy()
        if estimates.empty:
            st.info("The Sentinel-1 file contains no usable candidate estimates.")
        else:
            availability_rows = []
            validation_rows = []
            for lake, group in estimates.groupby("key"):
                group = group.sort_values("date")
                scored = group.dropna(subset=["official_storage_mcft", "estimate_error_mcft"])
                errors = scored["estimate_error_mcft"].to_numpy(dtype=float)
                latest = group.iloc[-1]
                availability_rows.append({
                    "Reservoir": RESERVOIRS[lake][0],
                    "Candidate scenes": len(group),
                    "First scene": group["date"].min().strftime("%Y-%m-%d"),
                    "Latest scene": latest["date"].strftime("%Y-%m-%d"),
                    "Latest estimate (mcft)": float(latest["satellite_estimate_mcft"]),
                })
                if len(errors):
                    capacity = RESERVOIRS[lake][1]
                    validation_rows.append({
                        "Reservoir": RESERVOIRS[lake][0],
                        "Exact-date holdout pairs": len(errors),
                        "RMSE (mcft)": float((errors ** 2).mean() ** 0.5),
                        "RMSE (% capacity)": float((errors ** 2).mean() ** 0.5 / capacity * 100),
                        "MAE (mcft)": float(abs(errors).mean()),
                        "Bias (mcft)": float(errors.mean()),
                        "Selected VV/VH area rule": ", ".join(
                            sorted(scored["selected_area_variant"].dropna().astype(str).unique())
                        ),
                    })

            st.subheader("Available Sentinel-1 candidate estimates")
            st.dataframe(
                pd.DataFrame(availability_rows).round({
                    "Latest estimate (mcft)": 1,
                }), width="stretch", hide_index=True,
            )
            st.subheader("Exact-date holdout performance")
            st.caption(
                "The table covers all reservoirs. The chart below follows the reservoir "
                "selected in the sidebar and shows only Sentinel-1 acquisition dates."
            )
            if validation_rows:
                st.dataframe(
                    pd.DataFrame(validation_rows).round({
                        "RMSE (mcft)": 1, "RMSE (% capacity)": 1,
                        "MAE (mcft)": 1, "Bias (mcft)": 1,
                    }), width="stretch", hide_index=True,
                )
            else:
                st.info("No exact-date CMWSSB holdout pairs are available for scoring yet.")

            lake_rows = estimates[estimates["key"] == key].sort_values("date")
            if not lake_rows.empty:
                chart = go.Figure()
                chart.add_trace(go.Scatter(
                    x=lake_rows["date"], y=lake_rows["satellite_estimate_mcft"],
                    mode="markers", name="Sentinel-1 candidate · scene dates",
                    marker=dict(color=SAT_COLOR, size=7),
                ))
                official_rows = lake_rows.dropna(subset=["official_storage_mcft"])
                if not official_rows.empty:
                    chart.add_trace(go.Scatter(
                        x=official_rows["date"], y=official_rows["official_storage_mcft"],
                        mode="markers", name="CMWSSB exact-date value",
                        marker=dict(color=OFFICIAL_COLOR, size=8, symbol="diamond"),
                    ))
                chart.add_hline(
                    y=RESERVOIRS[key][1], line_dash="dot", line_color="#7A8B91",
                    annotation_text="Full capacity",
                )
                chart.update_layout(title=f"Sentinel-1 research candidates · {RESERVOIRS[key][0]}")
                st.plotly_chart(style_fig(chart, height=400), width="stretch")
            st.caption(
                "Markers appear only on Sentinel-1 acquisition dates; unsampled days "
                "are left blank. The combined forecast feature keeps Sentinel-2 first "
                "and uses Sentinel-1 only when no usable S2 estimate exists on the "
                "same lake/date. S1 remains absent from the satellite storage cards, "
                "and the forecast backtest did not show a broad accuracy gain."
            )

st.divider()
st.caption("Official storage figures: CMWSSB via data.opencity.in. Sentinel-2 powers optical estimates. Sentinel-1 is radar and is used only as a same-date gap-fill lag feature in the live XGBoost forecast; it does not alter the satellite storage cards.")
