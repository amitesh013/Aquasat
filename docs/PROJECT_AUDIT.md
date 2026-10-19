# AquaSat project audit

**Historical snapshot:** this audit describes the project as it stood on 18 October 2026. The active clear95 Sentinel-2 dashboard path and validated `metrics.csv` were added on 19 October; see `README.md` and `AQUASAT_HANDOFF_GUIDE.md` for current behavior.

**Later update (19 October 2026):** the user authorized moving the combined S2-first/S1-fallback lag feature into the live 15-day XGBoost forecast. The audit findings below remain historical; the current generator is `generate_live_xgboost_forecast.py` and the dashboard consumes its `reservoir_forecasts.csv` output. The forecast backtest showed no broad accuracy gain from adding S1, which remains disclosed in the UI.

## Scope reviewed

Audit of the user-supplied `Aquasat.zip`, extracted to this working copy on 18 October 2026. The archive contains Python scripts, CSV outputs, two XLSX lookup workbooks, and one compiled cache file. It does not contain a Git history, `README`, dependency manifest, SRS, original/clean manual ground-truth file (only a merged file is present), `scrape_cmwssb.py`, test suite, or deployment configuration. The supplied project mirror under `sources/` was left untouched.

## Architecture as found

1. `extract_all.py` and `extract_poondi.py` use Google Earth Engine. `extract_all.py` targets the first day of each month, searches within ±7 days, filters by a scene cloud ceiling and chooses the least-cloudy qualifying scene. `extract_poondi.py` targets monthly storage dates and searches widening ±10/20/35-day windows. This is systematic monthly sampling of available scenes, not random date selection. The archive has 73–84 rows per reservoir (390 reservoir-observations total; 119 distinct acquisition dates across reservoirs). `extract_all.py` only outputs Chembarambakkam and Cholavaram; the supplied scripts do not clearly reproduce the included Poondi, Puzhal, and Veeranam `*_satellite_areas.csv` files.
2. `build_cmwssb.py` creates a lookup template from image dates and monthly historical hints. Its values are placeholders; it does not scrape CMWSSB.
3. `merge_ground.py` joins the manually populated lookup template to satellite records by image date and produces five calibration CSVs. In the included files, `mcft_exact_date` is blank for every row (0 populated rows across all five files), so these files do not currently provide calibration labels.
4. `prepare_data.py` independently matches `handwritten_ground_truth_merged.csv` to satellite areas on date and creates `dashboard_data.csv` and `metrics.csv`. This is the active calibration/dashboard preparation path in the supplied files.
5. `forcast_reservoirs.py` creates the dashboard's `reservoir_forecasts.csv` using historical monthly change patterns, with a recent damped trend when nearby official observations exist. It does not run Prophet or XGBoost.
6. `forcast_baseline.py` is an experiment that imports Prophet and XGBoost and writes `forecast_baselines.csv`; neither dependency is included in the ZIP or available in the current workspace runtime.
7. `app.py` is the Streamlit entry point. It loads `dashboard_data.csv`, `reservoir_forecasts.csv`, and `metrics.csv` from its working directory.

## Functional and partially functional areas

- The supplied dashboard has an overview/explorer/forecast/methodology interface in one Streamlit script. The fallback hardcoded RMSE/model values have been removed in this copy. Expired forecast dates are filtered and surfaced as unavailable; the stored forecast file has no generation metadata, so a future-looking but old output cannot be detected reliably.
- Satellite area CSVs and dashboard outputs are present. Satellite observations run through 1 September 2026 in the reservoir CSVs; dashboard records extend through 18 September 2026. Forecast files contain 75 rows (15 per reservoir) but no model metadata, generation time, input cutoff, or horizon columns.
- Five calibration CSVs are present and carry exact-date match fields, but all five have zero non-empty `mcft_exact_date` labels. Their maximum dates are in November or December 2025, while satellite observations continue to September 2026.
- `prepare_data.py` matches the merged ground-truth values to satellite areas by exact date. The forecast generator separately uses historical monthly series as its backbone; these have monthly temporal precision and should not be presented as daily measurements.
- `calibrate_all.py` expects the absent `handwritten_ground_truth_clean.csv`; it also uses random train/test splitting and hardcodes GP versus power-law selection. Its stated evaluation is not chronological.
- `forcast_baseline.py` contains Prophet and XGBoost experiments, but the deployed forecast script is the seasonal/damped-trend implementation. These methods are not interchangeable.

## Data quality and consistency findings

- There are 27 CSVs in the archive. Their schemas, rows, date ranges and direct/dynamic Python references are recorded in [CSV_INVENTORY.md](CSV_INVENTORY.md).
- No byte-identical CSV duplicates were found. Several similarly named files are experiments or alternate processing stages and cannot safely be removed without lineage evidence.
- `handwritten_ground_truth_merged.csv` is present (136 rows, 7 columns), but the original manually transcribed source and the `handwritten_ground_truth_clean.csv` expected by legacy `calibrate_all.py` are absent. Active `prepare_data.py` uses the merged file, so its calibration can be regenerated from included labels after installing dependencies; preservation against the original manual source cannot be verified.
- The generated calibration files include exact-date match columns, but some source provenance is only implied by file naming; monthly hints must not be treated as exact storage values.
- Latest satellite dates are 1 September 2026 for all five reservoirs. This is stale relative to the runtime date of 18 October 2026. The dashboard observations extend to 18 September 2026; forecast freshness needs to be checked independently.
- The legacy `calibrate_all.py` still uses a random split and does not preserve chronological order. The active `prepare_data.py` was later changed to expanding-window chronological validation; the included `metrics.csv` has no `cv_method` field, so its validation method cannot be confirmed from that output alone.
- Cholavaram extraction CSVs reach geofence fill ratios near saturation in the included material; calibration CSVs exclude or filter some near-saturated values but do not resolve whether the geofence is clipped or the reservoir truly varies little.
- Chembarambakkam/Puzhal coverage needs imagery-level validation; the outputs contain fill-ratio fields, but no imagery fixtures or masks are included for visual verification.
- The existing forecast output's last dates were inspected separately and expire relative to the current date. It should not be presented as a current forecast without regeneration.

## Priority fixes

1. Preserve and locate the missing original and cleaned manual ground-truth sources; establish cell-level provenance before updating values.
2. Replace the placeholder lookup workflow with a date-verifying, header-based scraper. The added `scrape_cmwssb.py` refuses unknown headers/dates, preserves populated manual fields, writes atomically, supports dry run, and keeps a JSON conflict/rejection report. A live read-only request was attempted but DNS resolution failed, so integration remains unverified.
3. Rebuild the five empty lookup-based calibration CSVs from verified exact-date records, keeping monthly values separately identified.
4. Re-evaluate calibrations with chronological holdouts and low-storage reporting; do not use the existing hardcoded/random-split results as final model evidence.
5. Align dashboard outputs and metadata to the actual active trend/seasonal forecast. Add Prophet/XGBoost only after dependencies and common verified evaluation windows are available.
6. Add dependency and deployment instructions; install Streamlit/Plotly and run the app in an environment that has those packages.
7. Investigate saturated and partial-coverage reservoirs with representative image review before changing thresholds or calibration methods.

## Audit result

The ZIP is a research prototype with a working intended entry point, but the supplied files do not establish a reproducible end-to-end build or deployment-ready status. The scraper correction and deterministic offline tests are now included in this copy. No original CSV was deleted or overwritten.
