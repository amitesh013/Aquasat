// AquaSat v3 Sentinel-2 extraction for all five Chennai reservoirs.
// Paste into https://code.earthengine.google.com, click Run, then start the
// CSV export from the Tasks tab. This does not edit any existing project CSV.

var PROJECT = 'aquasat-508704';
var BOUNDARIES_ASSET =
    'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3';
var S2_COLLECTION = 'COPERNICUS/S2_HARMONIZED';
var CLOUD_SCORE_COLLECTION = 'GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED';
var START_DATE = '2016-01-01';
// Earth Engine filterDate uses an exclusive end date; this includes 18 Oct 2026.
var END_DATE = '2026-10-19';
var CLEAR_SCORE_BAND = 'cs_cdf';
var CLEAR_SCORE_THRESHOLD = 0.60;
var MIN_CLEAR_AREA_FRACTION = 0.50;
var MAX_SCENE_CLOUD_PERCENT = 90;
var WATER_THRESHOLD = 0;
var RESERVOIRS = [
  'poondi', 'cholavaram', 'puzhal', 'chembarambakkam', 'veeranam'
];

var boundaries = ee.FeatureCollection(BOUNDARIES_ASSET);
var cloudScores = ee.ImageCollection(CLOUD_SCORE_COLLECTION)
    .filterDate(START_DATE, END_DATE);
var s2 = ee.ImageCollection(S2_COLLECTION)
    .filterDate(START_DATE, END_DATE)
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', MAX_SCENE_CLOUD_PERCENT));

function extractReservoir(reservoir) {
  var boundary = ee.Feature(
      boundaries.filter(ee.Filter.eq('name', reservoir)).first());
  var geometry = boundary.geometry();
  var boundaryArea = geometry.area(1);
  var isChembarambakkam = reservoir === 'chembarambakkam';
  var indexName = isChembarambakkam ? 'mndwi' : 'ndwi';
  var indexBands = isChembarambakkam ? ['B3', 'B11'] : ['B3', 'B8'];
  var scale = isChembarambakkam ? 20 : 10;

  var measured = s2.filterBounds(geometry).map(function(rawImage) {
    var image = ee.Image(rawImage).linkCollection(
        cloudScores, [CLEAR_SCORE_BAND]);
    var clear = image.select(CLEAR_SCORE_BAND).gte(CLEAR_SCORE_THRESHOLD);
    var water = image.normalizedDifference(indexBands).gt(WATER_THRESHOLD)
        .and(clear);
    var metricImage = clear.unmask(0).multiply(ee.Image.pixelArea())
        .rename('clear_area_sqm')
        .addBands(water.unmask(0).multiply(ee.Image.pixelArea())
            .rename('water_area_sqm'));
    var sums = metricImage.reduceRegion({
      reducer: ee.Reducer.sum(),
      geometry: geometry,
      scale: scale,
      maxPixels: 1e9,
      tileScale: 4
    });
    var clearArea = ee.Number(sums.get('clear_area_sqm', 0));
    var waterArea = ee.Number(sums.get('water_area_sqm', 0));
    var footprintFraction = image.geometry()
        .intersection(geometry, ee.ErrorMargin(1))
        .area(1)
        .divide(boundaryArea);

    return image.set({
      reservoir: reservoir,
      target_date: image.date().format('YYYY-MM-dd'),
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
      clear_area_fraction: clearArea.divide(boundaryArea),
      water_index: indexName,
      water_index_threshold: WATER_THRESHOLD,
      measurement_scale_m: scale,
      water_area_sqm: waterArea,
      water_area_hectares: waterArea.divide(10000),
      boundary_area_sqm: boundaryArea,
      geofence_fill_ratio: waterArea.divide(boundaryArea)
    });
  }).filter(ee.Filter.gte('image_footprint_fraction', 0.995))
    .filter(ee.Filter.gte('clear_area_fraction', MIN_CLEAR_AREA_FRACTION));

  print(reservoir + ' v3 accepted Sentinel-2 scenes', measured.size());
  return ee.FeatureCollection(measured.map(function(image) {
    image = ee.Image(image);
    return ee.Feature(null, image.toDictionary([
      'reservoir', 'target_date', 'image_date', 'image_ymd', 'image_id',
      'boundary_version', 'source_collection', 'cloud_score_collection',
      'scene_cloud_percent', 'image_footprint_fraction', 'clear_score_band',
      'clear_score_threshold', 'clear_area_fraction', 'water_index',
      'water_index_threshold', 'measurement_scale_m', 'water_area_sqm',
      'water_area_hectares', 'boundary_area_sqm', 'geofence_fill_ratio'
    ]));
  }));
}

var allRows = ee.FeatureCollection([]);
RESERVOIRS.forEach(function(reservoir) {
  allRows = allRows.merge(extractReservoir(reservoir));
});

print('v3 candidate rows (one row per qualifying image; same-day granules retained)',
      allRows.size());
print('Rows by reservoir', allRows.aggregate_histogram('reservoir'));
print('Boundary version check', allRows.aggregate_histogram('boundary_version'));

Export.table.toDrive({
  collection: allRows,
  description: 'aquasat_v3_sentinel2_candidates_2016_2026',
  fileNamePrefix: 'aquasat_v3_sentinel2_candidates_2016_2026',
  fileFormat: 'CSV'
});
