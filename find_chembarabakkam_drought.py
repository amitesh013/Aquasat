import ee
import csv

ee.Initialize(project='aquasat-508704')

boundaries = ee.FeatureCollection(
    'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3'
)
geofence = boundaries.filter(ee.Filter.eq('name', 'chembarambakkam')).geometry()

WINDOWS = [
    ('2017-03-01', '2017-08-31', '2017_drought'),
    ('2019-04-01', '2019-09-30', '2019_dayZero'),
]

TILE = 'T44PLV'
BANDS = ['B3', 'B11']

results = []

for start, end, label in WINDOWS:
    print(f"\nScanning {label} ({start} to {end})...")

    collection = (
        ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        .filterBounds(geofence)
        .filterDate(start, end)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 25))
        .filter(ee.Filter.stringContains('system:index', TILE))
        .sort('system:time_start')
    )

    count = collection.size().getInfo()
    print(f"  Found {count} images under 25% cloud on tile {TILE}")

    if count == 0:
        print("  No usable images, skipping")
        continue

    image_list = collection.toList(count)

    for i in range(count):
        image = ee.Image(image_list.get(i)).clip(geofence)

        water_index    = image.normalizedDifference(BANDS).rename('WATER')
        water_mask     = water_index.gt(0)
        water_area_img = water_mask.multiply(ee.Image.pixelArea())

        area_result = water_area_img.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=geofence,
            scale=10,
            maxPixels=1e9,
        )

        water_area_sqm = area_result.get('WATER').getInfo()
        cloud_pct      = image.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()
        image_id       = image.get('system:index').getInfo()
        ymd            = image_id[:8]

        print(f"  {ymd}: cloud {cloud_pct:.1f}%, area {water_area_sqm/10000:.1f} ha")

        results.append({
            'label':          label,
            'image_id':       image_id,
            'ymd':            ymd,
            'cloud_percent':  round(cloud_pct, 2),
            'water_area_sqm': round(water_area_sqm, 2),
            'water_area_ha':  round(water_area_sqm / 10000, 2),
        })

with open('chembarambakkam_drought_candidates.csv', 'w', newline='') as f:
    fieldnames = ['label', 'image_id', 'ymd', 'cloud_percent', 'water_area_sqm', 'water_area_ha']
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)

print(f"\nDone! Saved {len(results)} rows to chembarambakkam_drought_candidates.csv")