import pandas as pd
import os

RESERVOIRS = ['poondi', 'chembarambakkam', 'puzhal', 'cholavaram', 'veeranam']
COMBINED_CSV = 'chennai_lakes_combined.csv'
OUTPUT_CSV = 'cmwssb_lookup_all.csv'

CMWSSB_LABEL = {
    'poondi': 'POONDI',
    'chembarambakkam': 'CHEMBARAMBAKKAM',
    'puzhal': 'RED HILLS',
    'cholavaram': 'CHOLAVARAM',
    'veeranam': 'VEERANAM',
}

MONTH_COLS = {
    1: 'Jan (In mcft)', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
    7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec',
}

LAKE_NAME = {
    'poondi': 'Poondi',
    'chembarambakkam': 'Chembarambakkam',
    'puzhal': 'Puzhal',
    'cholavaram': 'Cholavaram',
    'veeranam': 'Veeranam',
}

date_needs = {}

for r in RESERVOIRS:
    path = f'{r}_satellite_areas.csv'
    if not os.path.exists(path):
        print(f"missing {path}, skipping")
        continue
    df = pd.read_csv(path)
    for ymd in df['image_ymd'].astype(str):
        date_needs.setdefault(ymd, set()).add(r)

print(f"{len(date_needs)} unique dates across all reservoirs")

fallback = {}
if os.path.exists(COMBINED_CSV):
    cdf = pd.read_csv(COMBINED_CSV)
    for _, row in cdf.iterrows():
        lake = row['Lake']
        year = int(row['Year'])
        for m, col in MONTH_COLS.items():
            v = row.get(col)
            if pd.notna(v):
                fallback[(lake, year, m)] = float(v)

rows = []
for ymd in sorted(date_needs):
    y, m, d = int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8])
    cmwssb_date = f"{ymd[6:8]}-{ymd[4:6]}-{ymd[:4]}"
    needed = sorted(date_needs[ymd])
    row = {
        'image_ymd': ymd,
        'cmwssb_date': cmwssb_date,
        'lookup_url': f"https://cmwssb.tn.gov.in/lake-level?date={cmwssb_date}",
        'needed_for': ','.join(needed),
    }
    for r in RESERVOIRS:
        row[f'{r}_mcft'] = ''
        fb = fallback.get((LAKE_NAME[r], y, m))
        row[f'{r}_monthly_hint'] = fb if (r in needed and fb is not None) else ''
    rows.append(row)

cols = ['image_ymd', 'cmwssb_date', 'lookup_url', 'needed_for']
for r in RESERVOIRS:
    cols += [f'{r}_mcft', f'{r}_monthly_hint']

out = pd.DataFrame(rows)[cols]
out.to_csv(OUTPUT_CSV, index=False)

print(f"Saved template to {OUTPUT_CSV}")
print("\nrows needing each reservoir:")
for r in RESERVOIRS:
    n = sum(1 for v in date_needs.values() if r in v)
    print(f"  {r}: {n}  (CMWSSB row label: {CMWSSB_LABEL[r]})")