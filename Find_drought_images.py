import ee
import pandas as pd

ee.Initialize(project='aquasat-508704')

poondi_geofence = ee.FeatureCollection(
    'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3'
).filter(ee.Filter.eq('name', 'poondi')).geometry()

DROUGHT_WINDOWS = [
    ('2017-03-01', '2017-08-31'),
    ('2019-04-01', '2019-09-30'),
]
CLOUD_CEILING = 25
NDWI_THRESHOLD = 0
OUTPUT_CSV = 'poondi_drought_candidates.csv'
BOUNDARY_VERSION = 'v3'


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
    results = []
    for start, end in DROUGHT_WINDOWS:
        collection = (
            ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            .filterBounds(poondi_geofence)
            .filterDate(start, end)
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_CEILING))
            .sort('system:time_start')
        )
        n = collection.size().getInfo()
        print(f"{start} to {end}: {n} images under {CLOUD_CEILING}% cloud")

        if n == 0:
            continue

        image_list = collection.toList(n)

        for i in range(n):
            image = ee.Image(image_list.get(i))
            image_date = image.get('system:index').getInfo()
            cloud_pct = image.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()
            water_area_sqm = compute_water_area(image, poondi_geofence, NDWI_THRESHOLD)
            water_area_ha = water_area_sqm / 10000

            print(f"  {image_date}: cloud {cloud_pct:.1f}%, area {water_area_ha:.1f} ha")

            results.append({
                'image_date': image_date,
                'ymd': image_date[:8],
                'cloud_pct': cloud_pct,
                'water_area_sqm': water_area_sqm,
                'water_area_ha': water_area_ha,
                'boundary_version': BOUNDARY_VERSION,
                'source_collection': 'COPERNICUS/S2_SR_HARMONIZED',
            })

    out_df = pd.DataFrame(results)
    out_df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nSaved {len(out_df)} rows to {OUTPUT_CSV}")


if __name__ == '__main__':
    main()
