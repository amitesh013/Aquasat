import pandas as pd

from prepare_data import MODELS, cv_errors, summarize

KEYS = ["chembarambakkam", "cholavaram"]

for key in KEYS:
    df = pd.read_csv(f"{key}_threshold_sweep.csv")
    bands = [c for c in df.columns if c.startswith(("ndwi_", "mndwi_"))]
    rows = []
    for band in bands:
        matched = df[[band, "official"]].rename(columns={band: "area_ha", "official": "official_mcft"}).dropna()
        for name, Model in MODELS.items():
            s = summarize(cv_errors(Model, matched))
            if s is None:
                continue
            rows.append(
                {
                    "band": band,
                    "model": name,
                    "rmse": s["rmse"],
                    "low_rmse": s["low_rmse"],
                    "low_bias": s["low_bias"],
                    "score": s["score"],
                }
            )
    out = pd.DataFrame(rows).round(1).sort_values("score")
    print(f"\n{key}: best combinations")
    print(out.head(8).to_string(index=False))
    print(f"{key}: current setting (ndwi_0)")
    print(out[out["band"] == "ndwi_0"].to_string(index=False))