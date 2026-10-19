import pandas as pd
v = pd.read_csv("veeranam_satellite_areas.csv")
v["date"] = pd.to_datetime(v["image_ymd"].astype(str))
d = pd.read_csv("dashboard_data.csv", parse_dates=["date"])
o = d[d.reservoir == "veeranam"][["date", "official_mcft"]].dropna()
m = v[v.cloud_percent <= 25].merge(o, on="date")
print(m.groupby(pd.cut(m.official_mcft, [-1, 50, 200, 500, 900, 1200, 1500]), observed=True)["water_area_hectares"].agg(["count", "median", "min", "max"]).round(0))
print("max fill ratio:", m.geofence_fill_ratio.max())