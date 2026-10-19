import ee
import pandas as pd

ee.Initialize(project='aquasat-508704')

ASSET = 'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3'
KEYS = ['cholavaram', 'puzhal']
ALIASES = {'puzhal': ['puzhal', 'redhills', 'red hills']}
DATES_SOURCE = 'poondi_drought_candidates.csv'
TILE = 'T44PLV'
NDWI_THRESHOLD = 0


def find_geometry(features, key):
    names = ALIASES.get(key, [key])
    for f in features:
        values = [v.lower() for v in f['properties'].values() if isinstance(v, str)]
        if any(n in v for n in names for v in values):
            return ee.Geometry(f['geometry'])
    return None


def water_area_sqm(image, geometry):
    ndwi = image.clip(geometry).normalizedDifference(['B3', 'B8']).rename('NDWI')
    area = ndwi.gt(NDWI_THRESHOLD).multiply(ee.Image.pixelArea())
    result = area.reduceRegion(reducer=ee.Reducer.sum(), geometry=geometry, scale=10, maxPixels=1e9)
    return result.get('NDWI').getInfo()


def main():
    ymds = pd.read_csv(DATES_SOURCE)['ymd'].astype(str).tolist()
    features = ee.FeatureCollection(ASSET).getInfo()['features']
    for key in KEYS:
        geometry = find_geometry(features, key)
        if geometry is None:
            print(f'no boundary found for {key}; properties seen:')
            for f in features:
                print('  ', f['properties'])
            continue
        rows = []
        for ymd in ymds:
            day = pd.Timestamp(ymd)
            collection = (
                ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                .filterBounds(geometry)
                .filterDate(day.strftime('%Y-%m-%d'), (day + pd.Timedelta(days=1)).strftime('%Y-%m-%d'))
                .filter(ee.Filter.stringContains('system:index', TILE))
            )
            if collection.size().getInfo() == 0:
                print(f'{key} {ymd}: no image')
                continue
            image = ee.Image(collection.first())
            image_id = image.get('system:index').getInfo()
            cloud = image.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()
            sqm = water_area_sqm(image, geometry)
            print(f'{key} {ymd}: cloud {cloud:.1f}%, area {sqm / 10000:.1f} ha')
            rows.append({
                'label': '2019_dayZero',
                'image_id': image_id,
                'ymd': ymd,
                'cloud_percent': cloud,
                'water_area_sqm': sqm,
                'water_area_ha': sqm / 10000,
            })
        out = f'{key}_drought_candidates.csv'
        pd.DataFrame(rows).to_csv(out, index=False)
        print(f'saved {len(rows)} rows to {out}')


if __name__ == '__main__':
    main()
