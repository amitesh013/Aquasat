import ee
import csv
import datetime

ee.Initialize(project='aquasat-508704')

boundaries = ee.FeatureCollection(
    'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3'
)
BOUNDARY_VERSION = 'v3'
SOURCE_COLLECTION = 'COPERNICUS/S2_SR_HARMONIZED'

RESERVOIRS = ['chembarambakkam', 'cholavaram']
INDEX_BANDS = {'chembarambakkam': ['B3', 'B11']}
DEFAULT_BANDS = ['B3', 'B8']
YEARS = range(2016, 2027)
MONTHS = range(1, 13)
END_DATE = datetime.date(2026, 9, 30)
CLOUD_CEILING = 40
WINDOW_DAYS = 7

target_dates = [
    datetime.date(y, m, 1)
    for y in YEARS for m in MONTHS
    if datetime.date(y, m, 1) <= END_DATE
]

for reservoir_name in RESERVOIRS:
    print(f"\n{'='*50}")
    print(f"Processing: {reservoir_name.upper()}")
    print(f"{'='*50}")

    bands = INDEX_BANDS.get(reservoir_name, DEFAULT_BANDS)
    index_name = 'mndwi' if bands[1] == 'B11' else 'ndwi'
    print(f"  water index: {index_name}")

    geofence = boundaries.filter(ee.Filter.eq('name', reservoir_name)).geometry()
    geofence_area_ha = geofence.area(maxError=1).getInfo() / 10000
    print(f"  geofence area: {geofence_area_ha:.1f} ha")

    results = []

    for target_date in target_dates:
        start = (target_date - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
        end = (target_date + datetime.timedelta(days=WINDOW_DAYS)).isoformat()

        collection = (
            ee.ImageCollection(SOURCE_COLLECTION)
            .filterBounds(geofence)
            .filterDate(start, end)
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_CEILING))
            .filter(ee.Filter.contains(leftField='.geo', rightValue=geofence))
            .sort('CLOUDY_PIXEL_PERCENTAGE')
        )

        if collection.size().getInfo() == 0:
            print(f"  {target_date}: no fully-covering image under {CLOUD_CEILING}% cloud, skipping")
            continue

        image = collection.first().clip(geofence)

        water_index = image.normalizedDifference(bands).rename('WATER')
        water_mask = water_index.gt(0)
        water_area_image = water_mask.multiply(ee.Image.pixelArea())

        area_result = water_area_image.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=geofence,
            scale=10,
            maxPixels=1e9,
        )

        water_area_sqm = area_result.get('WATER').getInfo()
        cloud_pct = image.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()
        image_date = image.get('system:index').getInfo()
        water_area_ha = water_area_sqm / 10000
        fill_ratio = water_area_ha / geofence_area_ha

        flag = ''
        if fill_ratio > 0.98:
            flag = '  [SATURATED - geofence may be too small]'

        print(f"  {target_date}: {image_date}, cloud {cloud_pct:.1f}%, "
              f"area {water_area_ha:.1f} ha ({fill_ratio*100:.0f}% of geofence){flag}")

        results.append({
            'target_date': target_date.isoformat(),
            'image_date': image_date,
            'image_ymd': image_date[:8],
            'boundary_version': BOUNDARY_VERSION,
            'source_collection': SOURCE_COLLECTION,
            'cloud_percent': round(cloud_pct, 2),
            'water_area_sqm': round(water_area_sqm, 2),
            'water_area_hectares': round(water_area_ha, 2),
            'geofence_fill_ratio': round(fill_ratio, 4),
            'water_index': index_name,
        })

    output_file = f'{reservoir_name}_satellite_areas.csv'
    fieldnames = ['target_date', 'image_date', 'image_ymd', 'cloud_percent',
                  'water_area_sqm', 'water_area_hectares', 'geofence_fill_ratio',
                  'water_index', 'boundary_version', 'source_collection']

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"  Saved {len(results)} usable rows to {output_file}")

print("\nAll reservoirs done!")
