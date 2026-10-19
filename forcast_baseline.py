import logging
import warnings

import numpy as np
import pandas as pd
from prophet import Prophet
from xgboost import XGBRegressor

warnings.filterwarnings("ignore")
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

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
MONTHLY_FILE = "chennai_lakes_combined.csv"
N_ORIGINS = 24
FEATURES = ["lag1", "lag2", "lag3", "lag12", "month"]
MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

monthly = pd.read_csv(MONTHLY_FILE)


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


def load_series(name):
    df = extract_monthly_series(RESERVOIR_COL[name]).drop_duplicates("date")
    s = df.set_index("date")["storage"].asfreq("MS")
    s = s.interpolate(limit=2, limit_area="inside")
    return s.clip(0, FULL_CAPACITY[name])


def build_frame(s):
    frame = pd.DataFrame({"y": s})
    for k in (1, 2, 3, 12):
        frame[f"lag{k}"] = s.shift(k)
    frame["month"] = frame.index.month
    frame["delta"] = frame["y"] - frame["lag1"]
    return frame


def prophet_predict(s, origin, cap):
    hist = s[s.index < origin].dropna()
    df = pd.DataFrame({"ds": hist.index, "y": hist.values, "floor": 0.0, "cap": float(cap)})
    model = Prophet(
        growth="logistic",
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        changepoint_prior_scale=0.05,
        seasonality_prior_scale=10,
        uncertainty_samples=0,
    )
    model.fit(df)
    future = pd.DataFrame({"ds": [origin], "floor": 0.0, "cap": float(cap)})
    return float(model.predict(future)["yhat"].iloc[0])


def evaluate(name):
    cap = FULL_CAPACITY[name]
    s = load_series(name)
    frame = build_frame(s)
    origins = frame.dropna().index[-N_ORIGINS:]
    rows = []
    for origin in origins:
        train = frame[frame.index < origin].dropna()
        row = frame.loc[origin]
        seasonal = train.groupby("month")["delta"].mean()
        xgb = XGBRegressor(n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8, random_state=42, verbosity=0)
        xgb.fit(train[FEATURES], train["delta"])
        preds = {
            "Persistence": row["lag1"],
            "Anchored seasonal": row["lag1"] + seasonal.get(row["month"], 0.0),
            "XGBoost": row["lag1"] + float(xgb.predict(frame.loc[[origin], FEATURES])[0]),
            "Prophet": prophet_predict(s, origin, cap),
        }
        for model_name, pred in preds.items():
            rows.append(
                {
                    "reservoir": name,
                    "origin": origin,
                    "model": model_name,
                    "pred": float(np.clip(pred, 0, cap)),
                    "actual": float(row["y"]),
                }
            )
    return pd.DataFrame(rows)


def main():
    frames = []
    for name in FULL_CAPACITY:
        print(f"evaluating {name}...")
        frames.append(evaluate(name))
    res = pd.concat(frames, ignore_index=True)
    res["err"] = res["pred"] - res["actual"]
    res["cap"] = res["reservoir"].map(FULL_CAPACITY)
    res["sq_pct"] = (res["err"] / res["cap"] * 100) ** 2
    res["abs_pct"] = (res["err"].abs() / res["cap"] * 100)
    res.drop(columns=["cap", "sq_pct", "abs_pct"]).to_csv("forecast_baselines.csv", index=False)
    rmse = res.groupby(["reservoir", "model"])["err"].apply(lambda e: float(np.sqrt(np.mean(e**2)))).unstack()
    rmse_pct = rmse.div(pd.Series(FULL_CAPACITY), axis=0) * 100
    order = ["Persistence", "Anchored seasonal", "XGBoost", "Prophet"]
    print(f"\nOne-month-ahead backtest, last {N_ORIGINS} months of the CMWSSB monthly data")
    print("RMSE as % of capacity (lower is better):")
    table = rmse_pct[order].round(1)
    table.loc["MEAN"] = table.mean().round(1)
    print(table.to_string())
    print("\nRMSE in mcft:")
    print(rmse[order].round(0).to_string())
    print("\nBest model per reservoir:")
    for name in FULL_CAPACITY:
        print(f"  {name}: {rmse_pct.loc[name, order].idxmin()}")
    print(f"Best on average: {table.loc['MEAN'].idxmin()}")


if __name__ == "__main__":
    main()