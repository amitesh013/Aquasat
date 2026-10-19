import ee
import pandas as pd
from datetime import datetime, timedelta

ee.Initialize(project='aquasat-508704')

poondi_geofence = ee.FeatureCollection(
    'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3'
).filter(ee.Filter.eq('name', 'poondi')).geometry()

STORAGE_CSV = 'poondi_storage.csv'
OUTPUT_CSV = 'poondi_satellite_vs_storage.csv'
BOUNDARY_VERSION = 'v3'
START_YEAR = 2016
NDWI_THRESHOLD = 0
SEARCH_WINDOWS_DAYS = (10, 20, 35)

MONTH_COLS = {
    1: 'Jan (In mcft)', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
    7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec',
}


def load_monthly_storage(path, start_year):
    df = pd.read_csv(path)
    records = []
    for _, row in df.iterrows():
        year = int(row['Year'])
        if year < start_year:
            continue
        for month, col in MONTH_COLS.items():
            value = row[col]
            if pd.isna(value):
                continue
            records.append({'year': year, 'month': month, 'mcft': float(value)})
    return records


def get_clearest_image(target_date, geometry, windows):
    for window in windows:
        start = (target_date - timedelta(days=window)).strftime('%Y-%m-%d')
        end = (target_date + timedelta(days=window)).strftime('%Y-%m-%d')
        collection = (
            ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            .filterBounds(geometry)
            .filterDate(start, end)
            .sort('CLOUDY_PIXEL_PERCENTAGE')
        )
        if collection.size().getInfo() > 0:
            return collection.first(), window
    return None, None


def compute_water_area(image, geometry, threshold):
    clipped = image.clip(geometry)
    ndwi = clipped.normalizedDifference(['B3', 'B8']).rename('NDWI')
    water_mask = ndwi.gt(threshold)
    water_area_image = water_mask.multiply(ee.Image.pixelArea())
    result = water_area_image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=geometry,
        scale=10,
        maxPixels=1e9,
    )
    return result.get('NDWI').getInfo()


def main():
    records = load_monthly_storage(STORAGE_CSV, START_YEAR)
    results = []
    for rec in records:
        target_date = datetime(rec['year'], rec['month'], 1)
        image, window_used = get_clearest_image(target_date, poondi_geofence, SEARCH_WINDOWS_DAYS)
        if image is None:
            print(f"{rec['year']}-{rec['month']:02d}: no image found within {max(SEARCH_WINDOWS_DAYS)} days, skipping")
            continue

        cloud_pct = image.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()
        image_date = image.get('system:index').getInfo()
        water_area_sqm = compute_water_area(image, poondi_geofence, NDWI_THRESHOLD)
        water_area_ha = water_area_sqm / 10000

        print(f"{rec['year']}-{rec['month']:02d}: image {image_date}, cloud {cloud_pct:.1f}%, "
              f"window ±{window_used}d, area {water_area_ha:.1f} ha, storage {rec['mcft']} mcft")

        results.append({
            'year': rec['year'],
            'month': rec['month'],
            'target_date': target_date.strftime('%Y-%m-%d'),
            'image_date': image_date,
            'cloud_pct': cloud_pct,
            'window_days': window_used,
            'water_area_sqm': water_area_sqm,
            'water_area_ha': water_area_ha,
            'mcft': rec['mcft'],
            'boundary_version': BOUNDARY_VERSION,
        })

    out_df = pd.DataFrame(results)
    out_df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved {len(out_df)} rows to {OUTPUT_CSV}")


if __name__ == '__main__':
    main()
