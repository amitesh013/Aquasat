import pandas as pd
import os

RESERVOIRS = ['poondi', 'chembarambakkam', 'puzhal', 'cholavaram', 'veeranam']
LOOKUP_CSV = 'cmwssb_lookup_all.csv'

lookup = pd.read_csv(LOOKUP_CSV, dtype={'image_ymd': str})

for r in RESERVOIRS:
    path = f'{r}_satellite_areas.csv'
    if not os.path.exists(path):
        print(f"missing {path}, skipping")
        continue

    sat = pd.read_csv(path, dtype={'image_ymd': str})
    sub = lookup[['image_ymd', 'cmwssb_date', f'{r}_mcft']].rename(
        columns={f'{r}_mcft': 'mcft_exact_date'}
    )

    merged = sat.merge(sub, on='image_ymd', how='left')
    merged['mcft_exact_date'] = pd.to_numeric(merged['mcft_exact_date'], errors='coerce')

    total = len(merged)
    filled = merged['mcft_exact_date'].notna().sum()
    usable = merged[
        merged['mcft_exact_date'].notna()
        & (merged['cloud_percent'] <= 25)
        & (merged['geofence_fill_ratio'] < 0.98)
    ]

    out_path = f'{r}_calibration_data.csv'
    merged.to_csv(out_path, index=False)

    print(f"{r}: {total} rows, {filled} with ground truth, {len(usable)} usable after filters")
    if len(usable) > 0:
        print(f"   area range: {usable['water_area_hectares'].min():.1f} - "
              f"{usable['water_area_hectares'].max():.1f} ha")
        print(f"   storage range: {usable['mcft_exact_date'].min():.0f} - "
              f"{usable['mcft_exact_date'].max():.0f} mcft")
    print(f"   saved {out_path}")