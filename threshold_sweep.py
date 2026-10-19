import ee
import numpy as np
import pandas as pd

ee.Initialize(project='aquasat-508704')

ASSET = 'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3'
KEYS = ['chembarambakkam', 'cholavaram']
ALIASES = {'puzhal': ['puzhal', 'redhills', 'red hills']}
INDICES = {'ndwi': ['B3', 'B8'], 'mndwi': ['B3', 'B11']}
THRESHOLDS = [0.0, 0.1, 0.2, 0.3, 0.4]
TILE = 'T44PLV'
GROUND_TRUTH = 'handwritten_ground_truth_merged.csv'
DATES_SOURCE = 'poondi_drought_candidates.csv'
LOW_STORAGE = 300


def find_geometry(features, key):
    names = ALIASES.get(key, [key])
    for f in features:
        values = [v.lower() for v in f['properties'].values() if isinstance(v, str)]
        if any(n in v for n in names for v in values):
            return ee.Geometry(f['geometry'])
    return None


def official_for(key):
    wide = pd.read_csv(GROUND_TRUTH)
    dates = pd.to_datetime(wide[wide.columns[0]], dayfirst=True, errors='coerce')
    names = ALIASES.get(key, [key])
    col = next(c for c in wide.columns[1:] if any(n in c.lower() for n in names))
    values = pd.to_numeric(wide[col], errors='coerce')
    return pd.DataFrame({'date': dates, 'official': values}).dropna().drop_duplicates('date')


def candidate_dates(key, official):
    monthly = pd.read_csv(f'{key}_satellite_areas.csv')
    monthly = monthly[monthly['cloud_percent'] <= 25]
    dates = set(pd.to_datetime(monthly['image_ymd'].astype(str)))
    dates |= set(pd.to_datetime(pd.read_csv(DATES_SOURCE)['ymd'].astype(str), format='%Y%m%d'))
    return sorted(dates & set(official['date']))


def band_stack(image, geometry):
    bands = []
    for name, pair in INDICES.items():
        index = image.normalizedDifference(pair)
        for t in THRESHOLDS:
            label = f'{name}_{int(round(t * 100))}'
            bands.append(index.gt(t).multiply(ee.Image.pixelArea()).rename(label))
    return ee.Image.cat(bands).clip(geometry)


def areas_for_day(geometry, day):
    collection = (
        ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(geometry)
        .filterDate(day.strftime('%Y-%m-%d'), (day + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
        .filter(ee.Filter.stringContains('system:index', TILE))
    )
    if collection.size().getInfo() == 0:
        return None
    image = ee.Image(collection.first())
    result = band_stack(image, geometry).reduceRegion(reducer=ee.Reducer.sum(), geometry=geometry, scale=10, maxPixels=1e9).getInfo()
    return {k: v / 10000 for k, v in result.items() if v is not None}


def report(key, df):
    cols = [c for c in df.columns if c.startswith(('ndwi_', 'mndwi_'))]
    low = df[df['official'] < LOW_STORAGE]
    rows = []
    for c in cols:
        rows.append(
            {
                'band': c,
                'spearman_all': df[c].corr(df['official'], method='spearman'),
                'spearman_low': low[c].corr(low['official'], method='spearman') if len(low) > 3 else np.nan,
                'median_ha_when_low': low[c].median(),
            }
        )
    print(f'\n{key}: {len(df)} dates, {len(low)} below {LOW_STORAGE} mcft')
    print(pd.DataFrame(rows).round(2).to_string(index=False))


def main():
    features = ee.FeatureCollection(ASSET).getInfo()['features']
    for key in KEYS:
        geometry = find_geometry(features, key)
        if geometry is None:
            print(f'no boundary found for {key}; properties seen:')
            for f in features:
                print('  ', f['properties'])
            continue
        official = official_for(key)
        dates = candidate_dates(key, official)
        rows = []
        for i, day in enumerate(dates, 1):
            areas = areas_for_day(geometry, day)
            print(f'{key} {i}/{len(dates)} {day:%Y-%m-%d}', 'ok' if areas else 'no image')
            if areas:
                rows.append({'date': day, **areas})
        df = pd.DataFrame(rows).merge(official, on='date')
        df.to_csv(f'{key}_threshold_sweep.csv', index=False)
        report(key, df)


if __name__ == '__main__':
    main()
