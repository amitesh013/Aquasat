// AquaSat v3 Sentinel-2 water-area threshold sweep.
// Paste into https://code.earthengine.google.com, click Run, then start the
// CSV export from the Tasks tab. This writes a separate candidate export.
//
// The clear-sky gate and the water-classification threshold are different:
// clear95 means at least 95% of the reservoir boundary has clear pixels;
// each NDWI/MNDWI threshold below changes which clear pixels count as water.

var PROJECT = 'aquasat-508704';
var BOUNDARIES_ASSET =
    'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3';
var S2_COLLECTION = 'COPERNICUS/S2_HARMONIZED';
var CLOUD_SCORE_COLLECTION = 'GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED';
var START_DATE = '2016-01-01';
// Earth Engine filterDate has an exclusive end date.
var END_DATE = '2026-10-20';
var CLEAR_SCORE_BAND = 'cs_cdf';
var CLEAR_SCORE_THRESHOLD = 0.60;
var MIN_CLEAR_AREA_FRACTION = 0.95;
var MAX_SCENE_CLOUD_PERCENT = 90;
var WATER_THRESHOLDS = [-0.10, 0.00, 0.10, 0.20, 0.30];
var MIN_IMAGE_FOOTPRINT_FRACTION = 0.995;
var RESERVOIRS = [
  'poondi', 'cholavaram', 'puzhal', 'chembarambakkam', 'veeranam'
];

var boundaries = ee.FeatureCollection(BOUNDARIES_ASSET);
var cloudScores = ee.ImageCollection(CLOUD_SCORE_COLLECTION)
    .filterDate(START_DATE, END_DATE);
var s2 = ee.ImageCollection(S2_COLLECTION)
    .filterDate(START_DATE, END_DATE)
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', MAX_SCENE_CLOUD_PERCENT));

function thresholdToken(value) {
  var hundredths = Math.round(Math.abs(value) * 100);
  var padded = ('000' + hundredths).slice(-3);
  return (value < 0 ? 'm' : 'p') + padded;
}

function areaBands(indexImage, clear, prefix) {
  var bands = ee.Image.constant(0).rename('placeholder');
  WATER_THRESHOLDS.forEach(function(threshold) {
    var name = prefix + '_' + thresholdToken(threshold) + '_sqm';
    var areaBand = indexImage.gt(threshold)
        .and(clear)
        .unmask(0)
        .multiply(ee.Image.pixelArea())
        .rename(name);
    bands = bands.addBands(areaBand);
  });
  return bands.select(prefix + '_.*_sqm');
}

function extractReservoir(reservoir) {
  var boundary = ee.Feature(
      boundaries.filter(ee.Filter.eq('name', reservoir)).first());
  var geometry = boundary.geometry();
  var boundaryArea = geometry.area(1);
  // Use each lake's existing quality-measurement scale for the same clear95
  // gate as the current pipeline. NDWI and MNDWI use their native pixel scales.
  var qualityScale = reservoir === 'chembarambakkam' ? 20 : 10;

  var measured = s2.filterBounds(geometry).map(function(rawImage) {
    var image = ee.Image(rawImage).linkCollection(
        cloudScores, [CLEAR_SCORE_BAND]);
    var clear = image.select(CLEAR_SCORE_BAND).gte(CLEAR_SCORE_THRESHOLD);
    var clearArea = clear.unmask(0)
        .multiply(ee.Image.pixelArea())
        .rename('clear_area_sqm')
        .reduceRegion({
          reducer: ee.Reducer.sum(),
          geometry: geometry,
          scale: qualityScale,
          maxPixels: 1e9,
          tileScale: 4
        }).get('clear_area_sqm');
    var footprintFraction = image.geometry()
        .intersection(geometry, ee.ErrorMargin(1))
        .area(1)
        .divide(boundaryArea);

    var ndwi = image.normalizedDifference(['B3', 'B8']);
    var mndwi = image.normalizedDifference(['B3', 'B11']);
    var ndwiStats = areaBands(ndwi, clear, 'ndwi').reduceRegion({
      reducer: ee.Reducer.sum(),
      geometry: geometry,
      // Use 20 m for both indices so index comparisons share a pixel scale.
      scale: 20,
      maxPixels: 1e9,
      tileScale: 4
    });
    var mndwiStats = areaBands(mndwi, clear, 'mndwi').reduceRegion({
      reducer: ee.Reducer.sum(),
      geometry: geometry,
      scale: 20,
      maxPixels: 1e9,
      tileScale: 4
    });

    var properties = {
      reservoir: reservoir,
      image_date: image.date().format('YYYY-MM-dd'),
      image_ymd: image.date().format('YYYYMMdd'),
      image_id: image.get('system:index'),
      boundary_version: 'v3',
      source_collection: S2_COLLECTION,
      cloud_score_collection: CLOUD_SCORE_COLLECTION,
      scene_cloud_percent: image.get('CLOUDY_PIXEL_PERCENTAGE'),
      image_footprint_fraction: footprintFraction,
      clear_score_band: CLEAR_SCORE_BAND,
      clear_score_threshold: CLEAR_SCORE_THRESHOLD,
      clear_area_fraction: ee.Number(clearArea).divide(boundaryArea),
      clear_quality_scale_m: qualityScale,
      ndwi_measurement_scale_m: 20,
      mndwi_measurement_scale_m: 20,
      boundary_area_sqm: boundaryArea
    };
    WATER_THRESHOLDS.forEach(function(threshold) {
      var token = thresholdToken(threshold);
      properties['ndwi_' + token + '_area_hectares'] =
          ee.Number(ndwiStats.get('ndwi_' + token + '_sqm')).divide(10000);
      properties['mndwi_' + token + '_area_hectares'] =
          ee.Number(mndwiStats.get('mndwi_' + token + '_sqm')).divide(10000);
    });
    return ee.Feature(null, properties);
  }).filter(ee.Filter.gte('image_footprint_fraction', MIN_IMAGE_FOOTPRINT_FRACTION))
    .filter(ee.Filter.gte('clear_area_fraction', MIN_CLEAR_AREA_FRACTION));

  print(reservoir + ' scenes before the local clear95 comparison', measured.size());
  return measured;
}

var allRows = ee.FeatureCollection([]);
RESERVOIRS.forEach(function(reservoir) {
  allRows = allRows.merge(extractReservoir(reservoir));
});

print('Threshold-sweep rows (one row per qualifying image; same-day granules retained)',
      allRows.size());
print('Rows by reservoir', allRows.aggregate_histogram('reservoir'));
print('Boundary versions', allRows.aggregate_histogram('boundary_version'));
print('Water thresholds', WATER_THRESHOLDS);

Export.table.toDrive({
  collection: allRows,
  description: 'aquasat_v3_sentinel2_threshold_sweep_2016_2026',
  fileNamePrefix: 'aquasat_v3_sentinel2_threshold_sweep_2016_2026',
  fileFormat: 'CSV'
});
