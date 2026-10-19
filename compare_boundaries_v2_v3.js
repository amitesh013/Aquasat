// Compare the five saved reservoir polygons in the AquaSat v2 and v3 assets.
// Paste into the Earth Engine Code Editor. This is read-only.

var v2 = ee.FeatureCollection(
  'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v2'
);
var v3 = ee.FeatureCollection(
  'projects/aquasat-508704/assets/chennai_reservoir_boundaries_v3'
);

print('V2 feature count', v2.size());
print('V3 feature count', v3.size());
print('Names in V2', v2.aggregate_array('name').sort());
print('Names in V3', v3.aggregate_array('name').sort());

var names = ee.List(v3.aggregate_array('name')).sort();
var comparison = ee.FeatureCollection(names.map(function(name) {
  name = ee.String(name);
  var oldFeature = ee.Feature(v2.filter(ee.Filter.eq('name', name)).first());
  var newFeature = ee.Feature(v3.filter(ee.Filter.eq('name', name)).first());
  var oldGeometry = oldFeature.geometry();
  var newGeometry = newFeature.geometry();
  var overlap = oldGeometry.intersection(newGeometry, ee.ErrorMargin(1));
  var addedInV3 = newGeometry.difference(oldGeometry, ee.ErrorMargin(1));
  var removedInV3 = oldGeometry.difference(newGeometry, ee.ErrorMargin(1));
  var oldArea = oldGeometry.area(1);
  var newArea = newGeometry.area(1);
  var overlapArea = overlap.area(1);
  var unionArea = oldGeometry.union(newGeometry, ee.ErrorMargin(1)).area(1);

  return ee.Feature(null, {
    name: name,
    v2_area_ha: oldArea.divide(10000),
    v3_area_ha: newArea.divide(10000),
    area_change_ha: newArea.subtract(oldArea).divide(10000),
    area_change_percent: newArea.subtract(oldArea).divide(oldArea).multiply(100),
    overlap_ha: overlapArea.divide(10000),
    intersection_over_union_percent: overlapArea.divide(unionArea).multiply(100),
    v3_added_area_ha: addedInV3.area(1).divide(10000),
    v2_area_removed_in_v3_ha: removedInV3.area(1).divide(10000),
    v2_perimeter_km: oldGeometry.perimeter(1).divide(1000),
    v3_perimeter_km: newGeometry.perimeter(1).divide(1000)
  });
}));

var sortedComparison = comparison.sort('name');
print('Per-reservoir boundary comparison (expand features for all columns)', sortedComparison);
print('Names (same order as the numeric lists below)', sortedComparison.aggregate_array('name'));
print('V2 area (ha)', sortedComparison.aggregate_array('v2_area_ha'));
print('V3 area (ha)', sortedComparison.aggregate_array('v3_area_ha'));
print('V3 minus V2 area (ha)', sortedComparison.aggregate_array('area_change_ha'));
print('Area change (%)', sortedComparison.aggregate_array('area_change_percent'));
print('Intersection over union (%)', sortedComparison.aggregate_array('intersection_over_union_percent'));
print('Area added in V3 (ha)', sortedComparison.aggregate_array('v3_added_area_ha'));
print('V2 area removed in V3 (ha)', sortedComparison.aggregate_array('v2_area_removed_in_v3_ha'));
print('Calculated total V2 area (ha)', sortedComparison.aggregate_sum('v2_area_ha'));
print('Calculated total V3 area (ha)', sortedComparison.aggregate_sum('v3_area_ha'));
print('Total per-reservoir v3-minus-v2 area (ha)', sortedComparison.aggregate_sum('area_change_ha'));

Map.centerObject(v3, 9);
Map.addLayer(v2.style({color: '2878B5', fillColor: '2878B533', width: 2}), {}, 'V2 boundaries');
Map.addLayer(v3.style({color: 'E36B2C', fillColor: 'E36B2C33', width: 2}), {}, 'V3 boundaries');
