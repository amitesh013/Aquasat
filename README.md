# AquaSat

AquaSat is a React + FastAPI reservoir research dashboard backed only by Neon PostgreSQL. The public home page is at `/`, account creation is at `/signup`, sign-in is at `/login`, and authenticated dashboard views are at `/dashboard`. A single container serves the React frontend and API. The legacy Streamlit/research scripts remain in the repository but are not part of the deployed application.

## Run locally

Requirements: Python 3.10 or newer, Node.js 20.19+ (or 22.12+), npm, and the Neon connection string. Copy `.env.example` to `.env`, then set `DATABASE_URL`. `.env` is ignored by Git. For local HTTP development, leave `AQUASAT_COOKIE_SECURE=false`.

```powershell
python -m pip install -r requirements-api.txt
Set-ExecutionPolicy -Scope Process Bypass
.\run_react.ps1
```

Open `http://127.0.0.1:5173`. The launcher applies database migrations, starts FastAPI at `http://127.0.0.1:8765`, and runs the Vite development server. It does not re-upload source files every time the app starts. Create an account with a unique username, email, and password of at least 6 characters; then sign in with your username and password. Stop it with Ctrl+C. The API schema is at `http://127.0.0.1:8765/docs`.

Open `/signup` to create an account with a unique username, email, and password of at least 6 characters. Existing accounts receive a username based on their email when the new migration runs; sign in using that username and password. Every account can view the same read-only research dashboard.

## Deploy with Docker

The Docker image builds the React app and serves it with FastAPI. The image intentionally excludes source CSVs and database credentials; dashboard data is read from Neon.

```powershell
docker build -t aquasat .
docker run --rm --env-file .env -p 8000:8000 aquasat
```

For a cloud container host, deploy this repository using its `Dockerfile` and set the server-side environment variables `DATABASE_URL` (Neon URL with SSL) and `AQUASAT_COOKIE_SECURE=true`. Do not put the database URL in React variables or commit it. The app applies Alembic migrations when the container starts. The reviewed dashboard snapshot is already imported; run `python sync_aquasat_database.py` only when intentionally refreshing that snapshot.

The Neon schema and reviewed dashboard snapshot are already loaded. It stores reservoirs, official and satellite observations, forecast rows, calibration metrics, and source-file hashes/sync times. The UI reads saved model outputs; Neon does not retrain a model or improve accuracy. Automated CMWSSB, IMD, or Earth Engine ingestion is not enabled. Accounts use a unique username and password (with an email address for the account); passwords are stored as Argon2id hashes, and server sessions are revocable and stored in Neon. Email verification and password recovery are not implemented yet. Registration is open, and all users see the same read-only dashboard rather than private per-user data.

## Deploy on Vercel

This repository includes `Dockerfile.vercel`, which builds the React frontend and runs FastAPI in the same container. Vercel's container deployment uses the `PORT` environment variable; the image defaults it to port 80. Connect the GitHub repository to Vercel and leave the project root at the repository root. Vercel builds and deploys the container on each deployment.

In Vercel **Project Settings → Environment Variables**, add these server-side variables for Production (and Preview too if you want preview deployments to work):

| Name | Value |
| --- | --- |
| `DATABASE_URL` | Neon PostgreSQL connection string, with SSL enabled |
| `AQUASAT_COOKIE_SECURE` | `true` |

Keep `DATABASE_URL` in Vercel's server-side environment settings; do not add it to `frontend/.env` or commit it. The database connection string is used by the API and migration startup. After the first deployment, check `/health`, open `/signup`, create your account, and confirm the dashboard loads. All persistent app data stays in Neon; the Vercel container itself is stateless. See Vercel's [Docker deployment guide](https://vercel.com/kb/docker).

The old Streamlit research interface can still be launched with `python -m streamlit run app.py` after installing `requirements.txt`; it is separate from the Neon-backed deployed React application.

AquaSat is a research demonstration for satellite-based reservoir storage estimation and forecasting for Chennai.

## Current state

The Streamlit entry point is `app.py`. It loads `dashboard_data.csv`, `metrics.csv`, and `reservoir_forecasts.csv` from this folder. Active satellite estimates use v3 Sentinel-2 clear95 scenes with per-reservoir NDWI/MNDWI thresholds selected on early dates, then isotonic storage calibration using only earlier exact-date CMWSSB matches. Accuracy is reported on the later 20% holdout. The 15-day forecast uses XGBoost with official storage history, an S2-first/S1-fallback satellite lag feature, and prior 7-, 30-, and 90-day rainfall from the separate merged GPM product. Satellite scenes run through 6 October 2026, official dashboard observations reach 16 October, and the merged GPM history is available through 20 October.

The active forecast generator is `generate_live_xgboost_forecast.py`, also selected by the default `forcast_reservoirs.py` action. It writes a 15-day XGBoost forecast using official-storage history, the latest prior satellite estimate, and complete antecedent rainfall totals. Rainfall on or after each forecast origin is excluded. The daily chart path interpolates to the model-generated day-15 endpoint. Its approximate shaded range currently uses the available S2/S1 rolling-origin reference errors and is not calibrated specifically for the rainfall-assisted model. `--model seasonal-baseline` explicitly selects the previous seasonal/damped-trend forecast. `build_clear95_dashboard.py` assembles the active dashboard history and matching holdout metrics from the prior-only Sentinel-2 candidate. `prepare_data.py` is a separate model-comparison pipeline and writes candidate outputs by default. Satellite-estimation errors and forecast-horizon errors are separate measures.

Current active Sentinel-2 later-date holdout (area rule selected on early dates; storage calibration uses earlier exact-date matches):

| Reservoir | Holdout pairs | Active RMSE (mcft) | RMSE (% capacity) | Prior-rule RMSE (mcft) | Below-300 RMSE (pairs) |
|---|---:|---:|---:|---:|
| Poondi | 41 | 246.0 | 7.6% | 617.0 | — (0) |
| Cholavaram | 42 | 55.8 | 5.2% | 40.3 | 40.7 (25) |
| Puzhal | 37 | 356.8 | 10.8% | 442.6 | — (0) |
| Chembarambakkam | 35 | 294.1 | 8.1% | 297.8 | — (0) |
| Veeranam | 31 | 197.4 | 13.5% | 312.7 | 22.5 (4) |

On the same 186 later-date pairs pooled across lakes, RMSE fell from 395.2 to 249.3 mcft (about 37%). Cholavaram was worse by 15.5 mcft on its holdout, so the change did not improve every lake. These are satellite-estimation errors, not forecast errors. Four reservoirs have no below-300 holdout pairs; Veeranam has only four, so drought-range performance is not established for them.

The selected water-area rules are Poondi MNDWI > 0.10, Cholavaram MNDWI > 0.10, Puzhal MNDWI > −0.10, Chembarambakkam MNDWI > −0.10, and Veeranam MNDWI > 0.00. These rules were selected on earlier dates from a 20-variant NDWI/MNDWI sweep; the table above reports their later-date holdout. In the saved 89-pair Puzhal comparison, XGBoost RMSE was 144.7 mcft without the S2 feature and 146.2 mcft with it. This is a reminder that satellite-assisted forecasting is not consistently more accurate; the live model and its limitations are described below.

### Rainfall research validation

The app's **Rainfall validation** tab shows the 2019 rainfall case study and the expanded 2019–2024 multi-year comparison. Daily IMD gridded rainfall at 0.25° resolution is extracted at the nearest cell to each representative reservoir-centre coordinate. This is a local proxy, not catchment-average precipitation, and rainfall alone does not measure reservoir inflow. Both comparisons use only antecedent 7-, 30-, and 90-day totals; rainfall on or after a forecast origin is excluded.

The expanded backtest produced 344 comparable 15-day forecast pairs for target years 2019–2024. Pooled RMSE was **221.49 mcft without rainfall** and **225.84 mcft with rainfall** (+4.35 mcft); capacity-normalized pooled RMSE was **10.46%** and **10.48%**. The year and reservoir results were mixed. This study uses the standard archive and is separate from the merged GPM series now used by the live model. The earlier Jan–Aug 2019 window is reported separately: 33 pairs, with pooled RMSE 193.57 mcft without rainfall and 195.52 mcft with rainfall. Data source: [IMD daily 0.25° rainfall archive](https://imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html).

As of 21 October 2026, the live 15-day XGBoost forecast also uses prior 7-, 30-, and 90-day totals from the separate IMD/NCMRWF merged GPM product. It uses official storage history and the S2-first/S1-fallback satellite feature as well. The daily nearest-cell rainfall history is complete through 20 October; rainfall on or after each forecast origin is excluded. These readings are local rainfall proxies, not catchment averages. The saved comparison is a pooled-model research comparison without satellite features and is not proof that rainfall improves the active combined model. Forecast ranges still use the available S2/S1 rolling-origin reference errors until the rainfall-assisted model has its own longer backtest.

Rebuild the 2016–2024 nearest-cell rainfall history and rerun the multi-year comparison with:

```powershell
python build_imd_rainfall.py --start-year 2016 --end-year 2024 --output rainfall_reservoir_daily_2016_2024.csv
python backtest_2019_rainfall.py --rainfall rainfall_reservoir_daily_2016_2024.csv --start-date 2019-01-01 --end-date 2024-12-31 --predictions-output rainfall_2019_2024_predictions_candidate.csv --summary-output rainfall_2019_2024_summary_candidate.csv --yearly-summary-output rainfall_2019_2024_by_year_candidate.csv
```

The first command fetches annual IMD NetCDF files only when they are not already cached, then writes the compact CSV used by the app. The second writes research candidates only; it does not overwrite dashboard history or the live forecast.

### V3 Sentinel-2 storage-estimation pipeline

`build_sentinel2_storage.py` turns the original v3 clear95 area data into a prior-only series. `build_sentinel2_threshold_storage.py` uses the early-selected area rule for each lake, then applies prior-only isotonic storage calibration. Both exclude disputed labels and require at least 95% clear reservoir coverage and 99.5% image-footprint coverage. The threshold builder reports the later 20% holdout because those rules were selected on the early dates.

PowerShell example (run from this folder):

```powershell
python build_sentinel2_storage.py --candidates "$env:USERPROFILE\Downloads\aquasat_v3_sentinel2_candidates_2016_2026_clear95.csv" --matches sentinel2_v3_clear95_accuracy_ready.csv --conflicts ground_truth_conflicts_for_review.csv --output sentinel2_v3_clear95_oos_storage_candidate.csv
python backtest_15day_forecasts.py --ground-truth reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv --satellite sentinel2_v3_clear95_oos_storage_candidate.csv --conflicts ground_truth_conflicts_for_review.csv --skip-prophet --output forecast_backtest_s2_integrated_candidate.csv
```

These commands generate research candidates only. The candidate acquisition file must contain v3 boundary provenance, scene date, water area, clear-area fraction, and image-footprint fraction. Review both outputs before considering dashboard promotion.

To assemble the original-area clear95 comparison candidate (this is not the active threshold-selected series), use candidate output paths:

```powershell
python build_clear95_dashboard.py --estimates sentinel2_v3_clear95_oos_storage_candidate.csv --history dashboard_data.csv --dashboard-output dashboard_data_original_area_candidate.csv --metrics-output metrics_original_area_candidate.csv
```

This preserves all existing official observations and writes matching validation metrics. The forecast file is not changed. Keep a copy of the existing dashboard and metrics files if you want a quick rollback.

To investigate whether the Sentinel-2 area-to-storage calibration can improve, compare isotonic, power-law, offset power-law, and Gaussian-process calibrators. It chooses a method from the first 80% of dates per lake, then reports performance on the later 20% holdout. The comparison requires all methods to predict the same dates for a fair score, excludes disputed readings, and writes predictions to a separate candidate file:

```powershell
python compare_sentinel2_calibrators.py --matches sentinel2_v3_clear95_accuracy_ready.csv --conflicts ground_truth_conflicts_for_review.csv --output sentinel2_v3_clear95_calibrator_comparison_candidate.csv
```

Use the holdout result to check the early-date choice; do not select a new method from the holdout ranking and present that same holdout as independent validation. This comparison does not change the dashboard or the existing calibration output.

### Sentinel-2 water-area threshold review

`gee_extract_v3_threshold_sweep.js` exports separate NDWI and MNDWI areas at thresholds −0.10, 0.00, 0.10, 0.20, and 0.30 for each scene. Both indices are measured at 20 m. It keeps the v3 boundary, Cloud Score+ cutoff, and clear95 image-quality gate. The local comparison joins by reservoir, date, and exact Earth Engine image ID, calibrates with prior-only official labels, chooses a variant on early dates, and reports a later holdout.

Run the Earth Engine script in the Code Editor, start its Drive export task, and save the downloaded CSV in `Downloads`. Then run from this project folder:

```powershell
python compare_sentinel2_thresholds.py --candidates "$env:USERPROFILE\Downloads\aquasat_v3_sentinel2_threshold_sweep_2016_2026.csv" --matches sentinel2_v3_clear95_accuracy_ready.csv --conflicts ground_truth_conflicts_for_review.csv --output sentinel2_v3_clear95_threshold_comparison_candidate.csv
```

Review the selected variant against both the 20 m like-for-like baseline and `current_baseline`. Use early dates to choose; the later holdout checks whether that choice generalizes. The active satellite pipeline uses the selected rules. To rebuild it and assemble the dashboard files without changing the stored forecast, run:

```powershell
python build_sentinel2_threshold_storage.py --candidates aquasat_v3_sentinel2_threshold_sweep_2016_2026.csv --matches sentinel2_v3_clear95_accuracy_ready.csv --conflicts ground_truth_conflicts_for_review.csv --selection-report sentinel2_v3_clear95_threshold_comparison_v2_candidate.csv --output sentinel2_v3_threshold_selected_oos_candidate.csv
python build_clear95_dashboard.py --estimates sentinel2_v3_threshold_selected_oos_candidate.csv --history dashboard_data.csv --conflicts ground_truth_conflicts_for_review.csv --dashboard-output dashboard_data.csv --metrics-output metrics.csv
```

### Sentinel-1 SAR fallback candidate

Sentinel-1 is a radar fallback path for dates when an optical Sentinel-2 storage estimate is unavailable. It is a different sensor and does not calculate NDWI or MNDWI. The Earth Engine script filters dual-polarization VV/VH IW-mode 10 m GRD scenes over the trusted v3 boundaries, exports a small threshold sweep of low-backscatter water-area candidates, and records orbit and coverage provenance. These dB thresholds are candidates to validate, not universal water rules. Google Earth Engine provides calibrated, terrain-corrected GRD backscatter in dB and recommends filtering the collection by polarization, mode, resolution, and orbit metadata ([Sentinel-1 guide](https://developers.google.com/earth-engine/guides/sentinel1), [dataset catalog](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD)).

Paste `gee_extract_sentinel1_v3_candidates.js` into the Earth Engine Code Editor, run it, submit the Tasks export, and save the CSV in `Downloads`. Then run these commands from this project folder:

```powershell
python build_sentinel1_fallback.py --candidates "$env:USERPROFILE\Downloads\aquasat_v3_sentinel1_candidates_2016_2026.csv" --ground-truth reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv --conflicts ground_truth_conflicts_for_review.csv --sentinel2 sentinel2_v3_threshold_selected_oos_candidate.csv --output sentinel1_v3_oos_fallback_candidate.csv --combined-output sentinel2_sentinel1_oos_storage_candidate.csv
python backtest_15day_forecasts.py --ground-truth reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv --satellite sentinel2_sentinel1_oos_storage_candidate.csv --conflicts ground_truth_conflicts_for_review.csv --skip-prophet --output forecast_backtest_s2_s1_fallback_candidate.csv
```

The SAR validator chooses a threshold rule from early exact-date official matches within each lake/orbit group, scores the selected rule on later matches, and calibrates each image using only earlier CMWSSB labels. It emits only post-selection-cutoff SAR estimates. The combined file prefers a usable Sentinel-2 estimate on the same lake/date and uses Sentinel-1 for a missing S2 estimate on that same lake/date. S1 is a gap fill; it does not correct an inaccurate S2 estimate that is already present. The live forecast uses this combined history as a lag feature, but the backtest did not show a broad RMSE gain from S1. The app's “Satellite validation” tab displays S1 holdout results after the candidate file is generated.

### Which data the forecast model uses; where MNDWI fits

`reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv` supplies the non-disputed official storage history used to fit the forecast. `build_sentinel2_threshold_storage.py` calibrates early-selected water-area rules using only prior exact-date official matches. The live XGBoost generator reads the combined S2-first/S1-fallback out-of-sample estimate file and the matching rolling-origin backtest. XGBoost receives official-storage history and, when available, the latest earlier calibrated satellite storage estimate as a lag feature; it does not receive raw imagery, NDWI/MNDWI, or VV/VH pixels. Sentinel-1 is used only when a usable S2 estimate is missing for that same lake/date. The backtest found no broad accuracy gain from adding S1; the user chose to use the combined feature in the live app while retaining that result and warning in the UI.

### 15-day forecast model comparison

`backtest_15day_forecasts.py` compares persistence, seasonal-only, seasonal plus damped trend, Prophet, and XGBoost on the same monthly rolling-origin 15-day evaluation dates. It excludes disputed lake/date pairs listed in `ground_truth_conflicts_for_review.csv`, trains each model only with readings available at that forecast origin, and writes detailed predictions and errors. The live generator uses the saved `xgboost_satellite` 15-day residuals for its approximate display range. In the user-run 444-pair comparison, pooled RMSE was about 210.84 mcft with S2-only features and 210.93 mcft with S2/S1; that tiny worsening is not evidence that S1 improves forecast accuracy. These forecasts remain advisory.

The user also ran this comparison on the 344 common 2019–2024 target pairs with Prophet 1.4.0 enabled. Pooled RMSE was 675.6 mcft for Prophet, 221.7 for storage-only XGBoost, 221.5 for satellite-feature XGBoost, and 217.1 for the seasonal/damped-trend baseline. Prophet did not include rainfall and was substantially worse, so it remains research-only. The detailed candidate output is `forecast_backtest_prophet_2019_2024_candidate.csv`.

```powershell
# Refresh the active 15-day XGBoost forecast using the latest S2-first/S1-fallback candidate.
python forcast_reservoirs.py

# Optional: preserve the former seasonal/damped-trend forecast instead.
python forcast_reservoirs.py --model seasonal-baseline

# Rebuild the backtest file first if the satellite candidate is refreshed.
python -m pip install -r requirements-research.txt
python backtest_15day_forecasts.py --ground-truth reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv --satellite sentinel2_sentinel1_oos_storage_candidate.csv --conflicts ground_truth_conflicts_for_review.csv --skip-prophet --output forecast_backtest_s2_s1_candidate.csv
```

To refresh the combined candidate itself, run the Sentinel-1 builder with `--sentinel2 sentinel2_v3_threshold_selected_oos_candidate.csv` before rerunning the backtest and forecast generator. The app's forecast target remains official CMWSSB storage; the satellite estimate is only a lagged input. The daily line interpolates to the model's day-15 endpoint, and the shaded empirical error range is not a calibrated probability interval. The historical satellite line on the chart remains observed imagery-date estimates, not a forecast.

## Local setup

Use Python 3.10 or newer. The archive did not provide a dependency lock or requirements file; this working copy adds an unpinned `requirements.txt` for the dashboard and `requirements-research.txt` for processing/experiments.

Run from this project folder in PowerShell:

```powershell
python -m pip install -r requirements.txt
.\run_aquasat.ps1
```

The launcher checks the required dashboard files, Python version, and app packages before starting Streamlit. To launch manually instead, use `python -m streamlit run app.py`.

Install `requirements-research.txt` when running calibration, forecasting experiments, or Earth Engine scripts. Those workflows still require verified source data and, for Earth Engine, credentials/configuration. To run `prepare_data.py` against a separately reviewed ground-truth copy without replacing the original, set `AQUASAT_GROUND_TRUTH` to that file's path first.

### Satellite record audit and candidate refresh

The existing `*_satellite_areas.csv` files do not record the source collection ID. Their 2016 rows cannot have come from the current `extract_all.py` Surface Reflectance collection, which starts in 2017. `verify_satellite_records_gee.py` is a read-only check against Earth Engine; it needs an authenticated Earth Engine account and cannot prove which collection produced an old row when metadata is ambiguous.

`extract_satellite_v2_candidate.py` is pinned to the manually refined, trusted v3 boundary asset and generates a separate v3 candidate CSV for each reservoir using every available harmonized Sentinel-2 TOA acquisition from 2016 onward, Cloud Score+ per-pixel clear masking, a minimum 50% clear-area threshold, and a full-boundary image-footprint check. It keeps one best-clear image per acquisition date and records the boundary version, collection, image ID, mask threshold, image footprint coverage, clear coverage, index, and scale. It will not replace current satellite, calibration, dashboard, or forecast files. Review these candidates and rerun chronological calibration before promoting any new satellite measurements.

To query only a recent interval, `--start-date` and `--end-date` are inclusive; choose a new `--output-suffix` to preserve the existing full-history candidates. For example, this checks 7–21 October 2026 and writes five separate `*_v3_recent_candidate.csv` files:

```powershell
python extract_satellite_v2_candidate.py --start-date 2026-10-07 --end-date 2026-10-21 --output-suffix v3_recent_candidate
```

These recent files contain image-derived area candidates, not calibrated storage inputs. Run chronological calibration and a locked forecast comparison before connecting them to a live forecast.

Run the audit and candidate extraction from this project folder after authenticating Earth Engine:

```powershell
python verify_satellite_records_gee.py
python extract_satellite_v2_candidate.py
$env:AQUASAT_SATELLITE_SUFFIX = "_v3_candidate"
$env:AQUASAT_DASHBOARD_OUTPUT = "dashboard_data_v3_candidate.csv"
$env:AQUASAT_METRICS_OUTPUT = "metrics_v3_candidate.csv"
python prepare_data.py
```

The calibration command writes separate v3 candidate outputs. Clear the three PowerShell environment variables afterward with `Remove-Item Env:AQUASAT_SATELLITE_SUFFIX, Env:AQUASAT_DASHBOARD_OUTPUT, Env:AQUASAT_METRICS_OUTPUT` before returning to the normal project workflow. The geometry comparison script `compare_boundaries_v2_v3.js` remains available as a historical audit; project extraction uses v3.

To run deterministic scraper tests:

```powershell
python -m unittest discover -s tests -v
```

## Streamlit Community Cloud preparation

1. Restore the required source and generated CSVs, including `dashboard_data.csv`, `metrics.csv`, and `reservoir_forecasts.csv`, into the GitHub repository root (or update `app.py` paths consistently).
2. Add the app dependencies to `requirements.txt` in the repository. The ZIP supplied no manifest, and the dependencies were not installed or launch-tested here.
3. Push the repository to GitHub and create a Streamlit Community Cloud app that points to `app.py` on the chosen branch.
4. Confirm the app starts, check every reservoir and empty-data state, and verify the stale-data warning before sharing its URL.

No deployment or public URL has been created or verified.

## CMWSSB collection

The new scraper inspects the current website form to discover its method and named date field, then checks the displayed observation date and exact current-storage column. It rejects ambiguous layouts and preserves populated manual values when `--base` is provided. The live form flow has not been verified in this environment.

Examples:

```powershell
python scrape_cmwssb.py --from 2026-10-10 --to 2026-10-18 --output cmwssb_scraped_observations.csv --dry-run
python scrape_cmwssb.py --from 2026-10-10 --to 2026-10-18 --interval 2 --output cmwssb_scraped_observations.csv
python scrape_cmwssb.py --from 2026-10-10 --to 2026-10-18 --base handwritten_ground_truth_merged.csv --output reviewed_ground_truth.csv --dry-run
```

Review the JSON report and every conflict before using scraped values. If a date is unavailable or the response is unrecognized, the scraper records a rejection; do not substitute another date. Keep the original manual source separately and use a reviewed output path.

### Veeranam annual archive

`scrape_veeranam_archive.py` reads the official yearly Veeranam pages for 2016–2022 and writes a separate long-format review CSV plus a quality report. It does not modify `reviewed_ground_truth.csv`. Blank storage cells are counted and omitted; numeric zeroes are retained. Start with a dry run, review each year's counts and invalid rows, then run without `--dry-run` to write the candidate archive:

```powershell
python scrape_veeranam_archive.py --start-year 2016 --end-year 2022 --dry-run
python scrape_veeranam_archive.py --start-year 2016 --end-year 2022
```

The archive pages are not uniformly complete. In the pages checked on 18 October 2026, 2018 had only 31 nonblank storage values; check the generated report before merging any archive readings into calibration data.

## Data and reproducibility

See `docs/PROJECT_AUDIT.md`, `docs/CSV_INVENTORY.md`, `docs/CSV_CLEANUP_LOG.md`, and `docs/REQUIREMENTS_TRACEABILITY.md`. Keep original datasets unchanged and regenerate outputs from verified inputs. Earth Engine extraction requires credentials/configuration not included in the archive. No public deployment is currently configured or verified.
