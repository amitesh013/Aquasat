"""Build auditable, cloud-masked Sentinel-2 candidate records for all lakes.

This script writes versioned *_satellite_areas_vN_candidate.csv files. It intentionally
does not overwrite the existing satellite or dashboard inputs.

The harmonized Sentinel-2 Level-1C (TOA) collection is used for every year so
the time series does not switch from TOA to surface reflectance in 2017.
Every available acquisition is processed, rather than sampling one image per
month. Cloud Score+ cs_cdf is linked by system:index and used as a per-pixel
clear-sky mask. Duplicate same-day granules are reduced to the one with the
best clear-area coverage. This remains a candidate output, not a replacement.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path

import ee


PROJECT = "aquasat-508704"
# The project has adopted the manually refined v3 boundaries as its trusted
# source. Keep extraction pinned so an old shell variable cannot select v2.
BOUNDARY_VERSION = "v3"
BOUNDARIES_ASSET = "projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3"
OUTPUT_SUFFIX = f"_{BOUNDARY_VERSION}_candidate"
S2_COLLECTION = "COPERNICUS/S2_HARMONIZED"
CLOUD_SCORE_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
RESERVOIRS = ("poondi", "cholavaram", "puzhal", "chembarambakkam", "veeranam")
BOUNDARY_ALIASES = {"puzhal": ("puzhal", "redhills", "red hills")}
MNDWI_RESERVOIRS = {"chembarambakkam"}
START_YEAR = 2016
CLEAR_SCORE_BAND = "cs_cdf"
CLEAR_THRESHOLD = 0.60
MIN_CLEAR_AREA_FRACTION = 0.50
MAX_SCENE_CLOUD_PERCENT = 90.0
NDWI_THRESHOLD = 0.0
MNDWI_THRESHOLD = 0.0
FIELDS = (
    "reservoir",
    "target_date",
    "image_date",
    "image_ymd",
    "image_id",
    "boundary_version",
    "source_collection",
    "cloud_score_collection",
    "scene_cloud_percent",
    "image_footprint_fraction",
    "clear_score_band",
    "clear_score_threshold",
    "clear_area_fraction",
    "water_index",
    "water_index_threshold",
    "measurement_scale_m",
    "water_area_sqm",
    "water_area_hectares",
    "boundary_area_sqm",
    "geofence_fill_ratio",
)


def sum_value(image: ee.Image, band: str, geometry: ee.Geometry, scale: int):
    values = image.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=geometry,
        scale=scale,
        maxPixels=1_000_000_000,
        tileScale=4,
    )
    return values.get(band)


def main() -> None:
    ee.Initialize(project=PROJECT)
    boundaries = ee.FeatureCollection(BOUNDARIES_ASSET)
    boundary_features = boundaries.getInfo().get("features", [])
    cloud_scores = ee.ImageCollection(CLOUD_SCORE_COLLECTION)
    start_date = dt.date(START_YEAR, 1, 1)
    end_date = dt.date.today() + dt.timedelta(days=1)
    output_properties = [
        "image_id", "image_date", "image_ymd", "scene_cloud_percent",
        "image_footprint_fraction", "clear_area_fraction", "water_area_sqm",
    ]

    for reservoir in RESERVOIRS:
        aliases = BOUNDARY_ALIASES.get(reservoir, (reservoir,))
        matches = []
        for feature in boundary_features:
            strings = [
                value.strip().lower()
                for value in feature.get("properties", {}).values()
                if isinstance(value, str)
            ]
            if any(alias in value for alias in aliases for value in strings):
                matches.append(feature)
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one v3 boundary matching {reservoir!r} ({aliases}), found {len(matches)}. "
                "Check the Earth Engine boundary asset before extracting."
            )
        geometry = ee.Feature(matches[0]).geometry()
        boundary_area_sqm = geometry.area(maxError=1).getInfo()
        index = "mndwi" if reservoir in MNDWI_RESERVOIRS else "ndwi"
        bands = ["B3", "B11"] if index == "mndwi" else ["B3", "B8"]
        threshold = MNDWI_THRESHOLD if index == "mndwi" else NDWI_THRESHOLD
        scale = 20 if index == "mndwi" else 10
        print(f"\n{reservoir}: {BOUNDARY_VERSION} boundary, processing every {index.upper()} acquisition at {scale} m")
        source = (
            ee.ImageCollection(S2_COLLECTION)
            .filterBounds(geometry)
            .filterDate(start_date.isoformat(), end_date.isoformat())
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_SCENE_CLOUD_PERCENT))
        )
        linked_scores = cloud_scores.filterDate(start_date.isoformat(), end_date.isoformat())

        def add_quality(image):
            image = ee.Image(image).linkCollection(linked_scores, [CLEAR_SCORE_BAND])
            clear = image.select(CLEAR_SCORE_BAND).gte(CLEAR_THRESHOLD)
            footprint_fraction = (
                image.geometry()
                .intersection(geometry, ee.ErrorMargin(1))
                .area(maxError=1)
                .divide(boundary_area_sqm)
            )
            clear_area = sum_value(
                clear.unmask(0).multiply(ee.Image.pixelArea()).rename("clear_area"),
                "clear_area",
                geometry,
                scale,
            )
            water = image.normalizedDifference(bands).gt(threshold).And(clear)
            water_area = sum_value(
                water.unmask(0).multiply(ee.Image.pixelArea()).rename("water_area"),
                "water_area",
                geometry,
                scale,
            )
            image_date = image.date().format("YYYY-MM-dd")
            return image.set({
                "image_id": image.get("system:index"),
                "image_date": image_date,
                "image_ymd": image.date().format("YYYYMMdd"),
                "scene_cloud_percent": image.get("CLOUDY_PIXEL_PERCENTAGE"),
                "image_footprint_fraction": footprint_fraction,
                "clear_area_fraction": ee.Number(clear_area).divide(boundary_area_sqm),
                "water_area_sqm": water_area,
            })

        candidates = (
            source.map(add_quality)
            .filter(ee.Filter.gte("image_footprint_fraction", 0.995))
            .filter(ee.Filter.gte("clear_area_fraction", MIN_CLEAR_AREA_FRACTION))
            .sort("clear_area_fraction", False)
        )
        image_count = candidates.size().getInfo()
        if image_count == 0:
            print("  no scenes passed footprint and clear-pixel checks")
            output_rows: list[dict[str, object]] = []
        else:
            image_list = candidates.toList(image_count)
            feature_list = image_list.map(
                lambda item: ee.Feature(None, ee.Image(item).toDictionary(output_properties))
            )
            raw_features = ee.FeatureCollection(feature_list).getInfo().get("features", [])
            # Keep one observation per calendar day, choosing the granule with
            # the greatest clear fraction (then the lower scene-cloud value).
            best_by_date = {}
            for feature in raw_features:
                props = feature.get("properties", {})
                image_date = props.get("image_date")
                if not image_date:
                    continue
                rank = (
                    float(props.get("clear_area_fraction") or 0),
                    -float(props.get("scene_cloud_percent") or 0),
                )
                previous = best_by_date.get(image_date)
                if previous is None or rank > previous[0]:
                    best_by_date[image_date] = (rank, props)

            output_rows = []
            for image_date, (_, props) in sorted(best_by_date.items()):
                water_area_sqm = float(props.get("water_area_sqm") or 0.0)
                fill_ratio = water_area_sqm / boundary_area_sqm if boundary_area_sqm else None
                output_rows.append({
                    "reservoir": reservoir,
                    "target_date": image_date,
                    "image_date": image_date,
                    "image_ymd": props.get("image_ymd", image_date.replace("-", "")),
                    "image_id": props.get("image_id", ""),
                    "boundary_version": BOUNDARY_VERSION,
                    "source_collection": S2_COLLECTION,
                    "cloud_score_collection": CLOUD_SCORE_COLLECTION,
                    "scene_cloud_percent": round(float(props.get("scene_cloud_percent") or 0), 2),
                    "image_footprint_fraction": round(float(props.get("image_footprint_fraction") or 0), 4),
                    "clear_score_band": CLEAR_SCORE_BAND,
                    "clear_score_threshold": CLEAR_THRESHOLD,
                    "clear_area_fraction": round(float(props.get("clear_area_fraction") or 0), 4),
                    "water_index": index,
                    "water_index_threshold": threshold,
                    "measurement_scale_m": scale,
                    "water_area_sqm": round(water_area_sqm, 2),
                    "water_area_hectares": round(water_area_sqm / 10000, 2),
                    "boundary_area_sqm": round(float(boundary_area_sqm), 2),
                    "geofence_fill_ratio": round(float(fill_ratio), 4) if fill_ratio is not None else "",
                })
            print(f"  processed {image_count} scenes; kept {len(output_rows)} dates after daily deduplication")

        output_path = Path(f"{reservoir}_satellite_areas{OUTPUT_SUFFIX}.csv")
        with output_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(output_rows)
        print(f"Saved {len(output_rows)} candidate rows to {output_path.name}")


if __name__ == "__main__":
    main()
