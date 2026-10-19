import argparse
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

FULL_CAPACITY = {
    "poondi": 3231,
    "cholavaram": 1081,
    "puzhal": 3300,
    "chembarambakkam": 3645,
    "veeranam": 1465,
}
RESERVOIR_COL = {
    "poondi": "Poondi",
    "cholavaram": "Cholavaram",
    "puzhal": "Puzhal",
    "chembarambakkam": "Chembarambakkam",
    "veeranam": "Veeranam",
}
RESERVOIRS = list(FULL_CAPACITY)
FORECAST_DAYS = 15
ALERT_PCT = 20
USE_OFFICIAL_RECENT = True
LOW_Q = 0.10
HIGH_Q = 0.90
TREND_FACTOR = 0.5
TREND_MIN_GAP = 2
TREND_MAX_GAP = 12
BACKTEST_START = pd.Timestamp("2019-04-01")
BACKTEST_END = pd.Timestamp("2019-09-30")
MONTHLY_FILE = "chennai_lakes_combined.csv"
DASHBOARD_FILE = "dashboard_data.csv"
OUTPUT_FILE = "reservoir_forecasts.csv"
MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

monthly = pd.read_csv(MONTHLY_FILE)
dashboard = pd.read_csv(DASHBOARD_FILE, parse_dates=["date"])


def extract_monthly_series(col_name):
    df = monthly[monthly["Lake"] == col_name]
    rows = []
    for _, row in df.iterrows():
        year = int(row["Year"])
        for mon, m_num in MONTH_MAP.items():
            if mon == "Jan":
                val = row.get("Jan (In mcft)", None)
                if pd.isna(val):
                    val = row.get("Jan (in mcft)", None)
                if pd.isna(val):
                    val = row.get("Jan", None)
            else:
                val = row.get(mon, None)
            if pd.notna(val):
                try:
                    rows.append({"date": pd.Timestamp(year, m_num, 1), "storage": float(str(val).replace(",", ""))})
                except (ValueError, TypeError):
                    pass
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def recent_series(name, after):
    d = dashboard[(dashboard["reservoir"] == name) & (dashboard["date"] > after)].copy()
    if USE_OFFICIAL_RECENT:
        d["y"] = d["official_mcft"].combine_first(d["sat_estimate_mcft"])
    else:
        d["y"] = d["sat_estimate_mcft"]
    return d.dropna(subset=["y"])[["date", "y"]]


def build_combined_series(name):
    backbone = extract_monthly_series(RESERVOIR_COL[name])
    backbone = backbone[backbone["date"] >= "2003-01-01"].rename(columns={"storage": "y"})[["date", "y"]]
    recent = recent_series(name, backbone["date"].max())
    combined = pd.concat([backbone, recent], ignore_index=True).sort_values("date")
    combined = combined.drop_duplicates("date", keep="last").reset_index(drop=True)
    combined["y"] = combined["y"].clip(0, FULL_CAPACITY[name])
    return combined


def monthly_delta_table(name, before=None):
    df = extract_monthly_series(RESERVOIR_COL[name]).drop_duplicates("date")
    s = df.set_index("date")["storage"].asfreq("MS")
    if before is not None:
        s = s[s.index < before]
    delta = s.diff().shift(-1).dropna()
    table = delta.groupby(delta.index.month).agg(
        mean="mean",
        q_low=lambda x: x.quantile(LOW_Q),
        q_high=lambda x: x.quantile(HIGH_Q),
    )
    return table.reindex(range(1, 13)).fillna(0.0)


def project(last_date, last_val, table, days, cap):
    rows = []
    mean = low = high = 0.0
    for k in range(1, days + 1):
        day = last_date + pd.Timedelta(days=k)
        dim = day.days_in_month
        row = table.loc[day.month]
        mean += row["mean"] / dim
        low += row["q_low"] / dim
        high += row["q_high"] / dim
        yhat = float(np.clip(last_val + mean, 0, cap))
        lower = float(np.clip(last_val + min(low, mean), 0, cap))
        upper = float(np.clip(last_val + max(high, mean), 0, cap))
        rows.append({"ds": day, "yhat": yhat, "yhat_lower": min(lower, yhat), "yhat_upper": max(upper, yhat)})
    return pd.DataFrame(rows)


def official_series(name):
    d = dashboard[dashboard["reservoir"] == name].dropna(subset=["official_mcft"])
    return d.drop_duplicates("date").set_index("date")["official_mcft"].sort_index()


def recent_slope(obs, origin):
    if origin not in obs.index:
        return None
    prev = obs[(obs.index >= origin - pd.Timedelta(days=TREND_MAX_GAP)) & (obs.index <= origin - pd.Timedelta(days=TREND_MIN_GAP))]
    if prev.empty:
        return None
    return float((obs[origin] - prev.iloc[-1]) / (origin - prev.index[-1]).days)


def project_trend(last_val, slope, seasonal, cap):
    out = seasonal.copy()
    steps = np.arange(1, len(out) + 1)
    center = np.clip(last_val + TREND_FACTOR * slope * steps, 0, cap)
    down = (seasonal["yhat"] - seasonal["yhat_lower"]).to_numpy()
    up = (seasonal["yhat_upper"] - seasonal["yhat"]).to_numpy()
    out["yhat"] = center
    out["yhat_lower"] = np.clip(center - down, 0, cap)
    out["yhat_upper"] = np.clip(center + up, 0, cap)
    return out


def rmse(values):
    return float(np.sqrt(np.mean(np.square(values)))) if len(values) else float("nan")


def run_backtest():
    print(f"\n{'=' * 84}")
    print(f"BACKTEST: 2019 Day Zero window ({BACKTEST_START.date()} to {BACKTEST_END.date()}), about 15 days ahead")
    print("Seasonal changes learned only from CMWSSB data before the window; RMSE in mcft")
    print(f"{'=' * 84}")
    print(f"{'Reservoir':<18} {'Pairs':>6} {'Persistence':>13} {'Seasonal':>10} {'Damped trend':>14}")
    print("-" * 84)
    pooled = {"persistence": [], "seasonal": [], "trend": []}
    for name in RESERVOIRS:
        cap = FULL_CAPACITY[name]
        obs = official_series(name)
        table = monthly_delta_table(name, before=BACKTEST_START)
        errs = {"persistence": [], "seasonal": [], "trend": []}
        for origin, value in obs[(obs.index >= BACKTEST_START) & (obs.index <= BACKTEST_END)].items():
            window = obs[(obs.index >= origin + pd.Timedelta(days=12)) & (obs.index <= origin + pd.Timedelta(days=18))]
            slope = recent_slope(obs, origin)
            if window.empty or slope is None:
                continue
            target = min(window.index, key=lambda d: abs((d - origin).days - 15))
            days = (target - origin).days
            actual = obs[target]
            seasonal = project(origin, value, table, days, cap).iloc[-1]["yhat"]
            trend = float(np.clip(value + TREND_FACTOR * slope * days, 0, cap))
            for label, pred in (("persistence", value), ("seasonal", seasonal), ("trend", trend)):
                errs[label].append(pred - actual)
                pooled[label].append((pred - actual) / cap * 100)
        if not errs["persistence"]:
            print(f"{name:<18} no usable pairs")
            continue
        print(f"{name:<18} {len(errs['persistence']):>6} {rmse(errs['persistence']):>13.1f} {rmse(errs['seasonal']):>10.1f} {rmse(errs['trend']):>14.1f}")
    print("-" * 84)
    print(f"{'All lakes (% cap)':<18} {len(pooled['persistence']):>6} {rmse(pooled['persistence']):>12.1f}% {rmse(pooled['seasonal']):>9.1f}% {rmse(pooled['trend']):>13.1f}%")


def run_live_forecast():
    print(f"\n{'=' * 88}")
    print(f"LIVE FORECAST: {FORECAST_DAYS} days ahead (official values used for recent dates: {USE_OFFICIAL_RECENT})")
    print(f"{'=' * 88}")
    print(f"{'Reservoir':<18} {'Last date':>11} {'Last known':>11} {'Forecast':>9} {'Range':>15} {'Cap%':>6} {'Alert':>7} {'Method':>9}")
    print("-" * 88)
    frames = []
    for name in RESERVOIRS:
        cap = FULL_CAPACITY[name]
        series = build_combined_series(name)
        last_date = series["date"].iloc[-1]
        last_val = float(series["y"].iloc[-1])
        table = monthly_delta_table(name)
        seasonal = project(last_date, last_val, table, FORECAST_DAYS, cap)
        slope = recent_slope(official_series(name), last_date)
        if slope is None:
            future, method = seasonal, "seasonal"
        else:
            future, method = project_trend(last_val, slope, seasonal, cap), "trend"
        end = future.iloc[-1]
        cap_pct = end["yhat"] / cap * 100
        alert = "LOW" if cap_pct < ALERT_PCT else "OK"
        rng = f"{end['yhat_lower']:.0f}-{end['yhat_upper']:.0f}"
        print(f"{name:<18} {last_date.date()!s:>11} {last_val:>11.1f} {end['yhat']:>9.1f} {rng:>15} {cap_pct:>5.1f}% {alert:>7} {method:>9}")
        future["reservoir"] = name
        frames.append(future[["ds", "yhat", "yhat_lower", "yhat_upper", "reservoir"]])
    pd.concat(frames).to_csv(OUTPUT_FILE, index=False)
    print(f"\nSaved {OUTPUT_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Refresh AquaSat's stored 15-day forecast.")
    parser.add_argument(
        "--model",
        choices=("xgboost-satellite", "seasonal-baseline"),
        default="xgboost-satellite",
        help=(
            "Use the live XGBoost forecast with the S2-first/S1-fallback lag feature, "
            "or explicitly request the legacy seasonal/trend baseline."
        ),
    )
    args = parser.parse_args()
    if args.model == "xgboost-satellite":
        from generate_live_xgboost_forecast import generate_forecast

        generate_forecast()
        return
    run_backtest()
    run_live_forecast()


if __name__ == "__main__":
    main()
