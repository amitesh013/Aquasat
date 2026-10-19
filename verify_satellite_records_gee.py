"""Verify saved Sentinel-2 IDs and basic metadata against Earth Engine.

Read-only: checks existing *_satellite_areas.csv files and prints a JSON report.
It does not recompute water masks or modify any CSV.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import ee

PROJECT = "aquasat-508704"
COLLECTIONS = (
    "COPERNICUS/S2_SR_HARMONIZED",
    "COPERNICUS/S2_HARMONIZED",
)
RESERVOIRS = ("poondi", "cholavaram", "puzhal", "chembarambakkam", "veeranam")


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def fetch_metadata(collection_id: str, image_ids: list[str]) -> dict[str, dict]:
    if not image_ids:
        return {}
    images = (
        ee.ImageCollection(collection_id)
        .filter(ee.Filter.inList("system:index", image_ids))
        .map(lambda image: ee.Image(image).select([]))
        .getInfo()
    )
    found = {}
    for feature in images.get("features", []):
        props = feature.get("properties", {})
        image_id = props.get("system:index")
        if image_id:
            found[image_id] = props
    return found


def main() -> None:
    ee.Initialize(project=PROJECT)
    report = {"project": PROJECT, "read_only": True, "reservoirs": {}}

    for reservoir in RESERVOIRS:
        path = Path(f"{reservoir}_satellite_areas.csv")
        if not path.exists():
            report["reservoirs"][reservoir] = {"error": f"Missing file: {path.name}"}
            continue
        rows = load_rows(path)
        ids = sorted({r.get("image_date", "").strip() for r in rows if r.get("image_date", "").strip()})
        metadata: dict[str, tuple[str, dict]] = {}
        for collection_id in COLLECTIONS:
            for image_id, props in fetch_metadata(collection_id, ids).items():
                metadata.setdefault(image_id, (collection_id, props))

        missing, mismatched_cloud, invalid_rows = [], [], []
        valid_offsets = []
        for row in rows:
            image_id = row.get("image_date", "").strip()
            props_entry = metadata.get(image_id)
            if not props_entry:
                missing.append(image_id)
                continue
            collection_id, props = props_entry
            ymd = row.get("image_ymd", "").strip()
            if not image_id.startswith(ymd):
                invalid_rows.append({"image_id": image_id, "issue": "image_ymd does not match image ID"})
            try:
                offset = (date.fromisoformat(row["target_date"]) - date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))).days
                valid_offsets.append(offset)
                if not -7 <= offset <= 6:
                    invalid_rows.append({"image_id": image_id, "issue": f"image offset outside expected ±7-day search window: {-offset} days"})
            except (KeyError, ValueError):
                invalid_rows.append({"image_id": image_id, "issue": "invalid target_date/image_ymd"})
            try:
                saved_cloud = float(row["cloud_percent"])
                live_cloud = float(props["CLOUDY_PIXEL_PERCENTAGE"])
                if abs(saved_cloud - live_cloud) > 0.02:
                    mismatched_cloud.append({"image_id": image_id, "saved": saved_cloud, "earth_engine": live_cloud})
            except (KeyError, TypeError, ValueError):
                invalid_rows.append({"image_id": image_id, "issue": "cloud metadata missing or invalid"})

        report["reservoirs"][reservoir] = {
            "rows": len(rows),
            "unique_image_ids": len(ids),
            "ids_found_in_earth_engine": len(ids) - len(missing),
            "matched_collection_ids": sorted({meta[0] for meta in metadata.values()}),
            "missing_image_ids": missing,
            "cloud_metadata_mismatches": mismatched_cloud,
            "invalid_rows": invalid_rows,
            "image_offset_days_min_max": [min(valid_offsets), max(valid_offsets)] if valid_offsets else None,
        }

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
