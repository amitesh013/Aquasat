// AquaSat v3 Sentinel-1 SAR water-area candidates for all five reservoirs.
// Paste into the Earth Engine Code Editor, run it, then start the CSV export
// from the Tasks tab. This is a research candidate export; it does not replace
// Sentinel-2 records, reviewed CMWSSB data, dashboard data, or forecasts.

var BOUNDARIES_ASSET =
    'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3';
var S1_COLLECTION = 'COPERNICUS/S1_GRD';
var START_DATE = '2016-01-01';
// Earth Engine filterDate uses an exclusive end date; includes 19 Oct 2026.
var END_DATE = '2026-10-20';
var MIN_IMAGE_FOOTPRINT_FRACTION = 0.995;
var MEASUREMENT_SCALE_M = 10;
var MIN_INCIDENCE_ANGLE = 30;
var MAX_INCIDENCE_ANGLE = 45;
var RESERVOIRS = [
  'poondi', 'cholavaram', 'puzhal', 'chembarambakkam', 'veeranam'
];

// dB thresholds are deliberately exported as candidates, not claimed as
// universal water thresholds. Water often has low backscatter, but wind,
// vegetation, shoreline mixing, incidence angle, and orbit direction matter.
var VV_THRESHOLDS_DB = [-22, -18, -14, -10];
var VH_THRESHOLDS_DB = [-30, -26, -22, -18];

function thresholdToken(value) {
  var absoluteHundredths = Math.round(Math.abs(value) * 100);
  var padded = ('0000' + absoluteHundredths).slice(-4);
  return (value < 0 ? 'm' : 'p') + padded;
}

function areaBand(imageMask, name) {
  return imageMask.unmask(0)
      .multiply(ee.Image.pixelArea())
      .rename(name);
}

var boundaries = ee.FeatureCollection(BOUNDARIES_ASSET);
var s1 = ee.ImageCollection(S1_COLLECTION)
    .filterDate(START_DATE, END_DATE)
    .filter(ee.Filter.eq('instrumentMode', 'IW'))
    .filter(ee.Filter.eq('resolution_meters', 10))
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'));

function extractReservoir(reservoir) {
  var boundary = ee.Feature(
      boundaries.filter(ee.Filter.eq('name', reservoir)).first());
  var geometry = boundary.geometry();
  var boundaryArea = geometry.area(1);

  var scenes = s1.filterBounds(geometry).map(function(rawImage) {
    var image = ee.Image(rawImage);
    var vv = image.select('VV');
    var vh = image.select('VH');
    var angle = image.select('angle');
    var angleMask = angle.gte(MIN_INCIDENCE_ANGLE)
        .and(angle.lte(MAX_INCIDENCE_ANGLE));
    var valid = vv.mask().and(vh.mask()).and(angleMask);

    var areaStack = areaBand(valid, 'valid_area_sqm');
    VV_THRESHOLDS_DB.forEach(function(threshold) {
      var name = 'vv_' + thresholdToken(threshold) + '_area_sqm';
      areaStack = areaStack.addBands(areaBand(vv.lt(threshold).and(valid), name));
    });
    VH_THRESHOLDS_DB.forEach(function(threshold) {
      var name = 'vh_' + thresholdToken(threshold) + '_area_sqm';
      areaStack = areaStack.addBands(areaBand(vh.lt(threshold).and(valid), name));
    });
    VV_THRESHOLDS_DB.forEach(function(vvThreshold) {
      VH_THRESHOLDS_DB.forEach(function(vhThreshold) {
        var name = 'vv_' + thresholdToken(vvThreshold) +
            '_vh_' + thresholdToken(vhThreshold) + '_area_sqm';
        var bothLow = vv.lt(vvThreshold).and(vh.lt(vhThreshold)).and(valid);
        areaStack = areaStack.addBands(areaBand(bothLow, name));
      });
    });

    var sums = areaStack.reduceRegion({
      reducer: ee.Reducer.sum(),
      geometry: geometry,
      scale: MEASUREMENT_SCALE_M,
      maxPixels: 1e9,
      tileScale: 4
    });
    var validArea = ee.Number(sums.get('valid_area_sqm', 0));
    var footprintFraction = image.geometry()
        .intersection(geometry, ee.ErrorMargin(1))
        .area(1)
        .divide(boundaryArea);
    var properties = {
      reservoir: reservoir,
      image_date: image.date().format('YYYY-MM-dd'),
      image_ymd: image.date().format('YYYYMMdd'),
      image_id: image.get('system:index'),
      boundary_version: 'v3',
      source_collection: S1_COLLECTION,
      instrument_mode: image.get('instrumentMode'),
      resolution_meters: image.get('resolution_meters'),
      polarizations: image.get('transmitterReceiverPolarisation'),
      orbit_pass: image.get('orbitProperties_pass'),
      relative_orbit: image.get('relativeOrbitNumber_start'),
      incidence_angle_min_deg: MIN_INCIDENCE_ANGLE,
      incidence_angle_max_deg: MAX_INCIDENCE_ANGLE,
      image_footprint_fraction: footprintFraction,
      valid_area_fraction: validArea.divide(boundaryArea),
      measurement_scale_m: MEASUREMENT_SCALE_M,
      boundary_area_sqm: boundaryArea
    };

    VV_THRESHOLDS_DB.forEach(function(threshold) {
      var key = 'vv_' + thresholdToken(threshold) + '_area_sqm';
      properties[key.replace('_sqm', '_hectares')] =
          ee.Number(sums.get(key, 0)).divide(10000);
    });
    VH_THRESHOLDS_DB.forEach(function(threshold) {
      var key = 'vh_' + thresholdToken(threshold) + '_area_sqm';
      properties[key.replace('_sqm', '_hectares')] =
          ee.Number(sums.get(key, 0)).divide(10000);
    });
    VV_THRESHOLDS_DB.forEach(function(vvThreshold) {
      VH_THRESHOLDS_DB.forEach(function(vhThreshold) {
        var key = 'vv_' + thresholdToken(vvThreshold) +
            '_vh_' + thresholdToken(vhThreshold) + '_area_sqm';
        properties[key.replace('_sqm', '_hectares')] =
            ee.Number(sums.get(key, 0)).divide(10000);
      });
    });
    return image.set(properties);
  }).filter(ee.Filter.gte(
      'image_footprint_fraction', MIN_IMAGE_FOOTPRINT_FRACTION));

  var propertyNames = [
    'reservoir', 'image_date', 'image_ymd', 'image_id', 'boundary_version',
    'source_collection', 'instrument_mode', 'resolution_meters', 'polarizations',
    'orbit_pass', 'relative_orbit',
    'incidence_angle_min_deg', 'incidence_angle_max_deg',
    'image_footprint_fraction', 'valid_area_fraction', 'measurement_scale_m',
    'boundary_area_sqm'
  ];
  VV_THRESHOLDS_DB.forEach(function(threshold) {
    propertyNames.push('vv_' + thresholdToken(threshold) + '_area_hectares');
  });
  VH_THRESHOLDS_DB.forEach(function(threshold) {
    propertyNames.push('vh_' + thresholdToken(threshold) + '_area_hectares');
  });
  VV_THRESHOLDS_DB.forEach(function(vvThreshold) {
    VH_THRESHOLDS_DB.forEach(function(vhThreshold) {
      propertyNames.push('vv_' + thresholdToken(vvThreshold) +
          '_vh_' + thresholdToken(vhThreshold) + '_area_hectares');
    });
  });
  var features = ee.FeatureCollection(scenes.toList(scenes.size()).map(function(scene) {
    return ee.Feature(null, ee.Image(scene).toDictionary(propertyNames));
  }));
  print(reservoir + ' v3 dual-pol Sentinel-1 candidate scenes', features.size());
  return features;
}

var allRows = ee.FeatureCollection([]);
RESERVOIRS.forEach(function(reservoir) {
  allRows = allRows.merge(extractReservoir(reservoir));
});

print('Sentinel-1 candidate rows (one row per scene and reservoir)', allRows.size());
print('Rows by reservoir', allRows.aggregate_histogram('reservoir'));
print('Pass directions', allRows.aggregate_histogram('orbit_pass'));
print('Relative orbits', allRows.aggregate_histogram('relative_orbit'));
print('Boundary versions', allRows.aggregate_histogram('boundary_version'));
print('Valid-area fraction statistics',
      allRows.aggregate_stats('valid_area_fraction'));

Export.table.toDrive({
  collection: allRows,
  description: 'aquasat_v3_sentinel1_candidates_2016_2026',
  fileNamePrefix: 'aquasat_v3_sentinel1_candidates_2016_2026',
  fileFormat: 'CSV'
});
