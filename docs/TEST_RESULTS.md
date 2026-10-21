# Test results

## Offline test run

- Command: `python -m unittest discover -s tests -v`
- Result: **15 passed, 0 failed**.
- Scope: deterministic HTML fixtures and mocked failures for the CMWSSB parser, schema/date checks, capacity validation, merge safety, dry-run behavior and output safety.
- The live CMWSSB form and request flow were not invoked by these tests.

## Checks not run

- Dashboard launch: unavailable because Streamlit and Plotly are not installed in the supplied runtime.
- Dashboard freshness changes were syntax-checked only: expired forecast dates are filtered, missing metrics remain unavailable, and stale/missing forecast warnings are explicit. Interactive behavior is not verified.
- Earlier runtime check: Prophet/XGBoost were unavailable in the initial workspace environment. The user later ran XGBoost and Prophet 1.4.0 in their local environment; the current Prophet results are recorded below.
- Legacy `calibrate_all.py`: blocked because `handwritten_ground_truth_clean.csv` is absent. The active `prepare_data.py` uses the included merged file, but could not be run because scientific Python dependencies are unavailable in this runtime.
- Earth Engine extraction: credentials and runtime configuration are absent.
- Live CMWSSB scrape: attempted once for 18 October 2026; direct access failed at DNS resolution (`getaddrinfo failed`). The official page's rendered text was reviewed separately, but the live form submission and parser were not verified. The scraper inspects the form at runtime and rejects ambiguity.

## Live forecast update (19 October 2026)

- `generate_live_xgboost_forecast.py` ran successfully using the current non-disputed official history, combined S2-first/S1-fallback satellite candidate, and 15-day rolling-origin residual file.
- It wrote 75 forecast rows (15 days for each of five reservoirs) to `reservoir_forecasts.csv`, with model, origin, satellite-feature source/date/age, horizon, and backtest metadata. The previous forecast is preserved in `reservoir_forecasts_pre_xgboost_live_20261019.csv`.
- No unit or interactive Streamlit tests were run during this update. The S1-assisted backtest remains mixed and does not establish a forecast accuracy improvement.

## IMD rainfall case study (19 October 2026)

- Extracted 7,305 daily rainfall rows (five reservoir-centre grid cells for 2016–2019) from official IMD annual NetCDF files into `rainfall_reservoir_daily_2016_2019.csv`.
- Ran `backtest_2019_rainfall.py` on 33 exact-target 15-day forecast pairs from 1 January through 31 August 2019. Rain on the forecast-origin date was excluded. Pooled RMSE was 193.57 mcft for the current S2-first/S1-fallback XGBoost features and 195.52 mcft with antecedent rainfall features; capacity-normalized RMSE was 13.08% vs 13.22%. Results are mixed by lake and do not support a live model change.
- The rainfall extraction and comparison are research workflows. No unit tests or interactive Streamlit launch were performed for this update.

## Extended rainfall comparison and dashboard integration (19 October 2026)

- The user built 16,440 daily nearest-cell rainfall rows for the five reservoirs from 1 January 2016 through 31 December 2024, with no missing values in the extracted series.
- The user ran the time-forward XGBoost comparison on 344 exact-date forecast pairs with target years 2019–2024. Overall RMSE was 221.49 mcft without rainfall and 225.84 mcft with antecedent rainfall; capacity-normalized RMSE was 10.46% and 10.48%. Yearly results were mixed, so no live model change was made.
- `app.py` now loads the longer rainfall history when present and displays the multi-year candidate summary, annual pooled RMSE chart, and per-reservoir/year detail on the Rainfall validation tab. It continues to label rainfall as research-only.
- Script syntax was checked after the changes. The bundled workspace Python lacks XGBoost, and the interactive Streamlit app was not launched here; the new display needs user-side visual confirmation.

## Prophet 15-day comparison (19 October 2026)

- The user ran the existing Prophet-enabled `backtest_15day_forecasts.py` on the same 344 non-disputed rolling-origin forecast/actual pairs from 2019–2024. Prophet was fit using only official storage observations available before each forecast origin. No rainfall regressor was used.
- Pooled RMSE (mcft): persistence 251.2; seasonal 233.7; seasonal/damped trend 217.1; XGBoost 221.7; XGBoost with satellite lag 221.5; Prophet 675.6. Capacity-normalized RMSE for Prophet was 26.04%, versus 10.23% for seasonal/damped trend and 10.46% for XGBoost with satellite lag.
- Prophet performed substantially worse and remains research-only. This comparison does not evaluate rainfall as a Prophet regressor and does not change the live forecast.
- Detailed output: `forecast_backtest_prophet_2019_2024_candidate.csv` (344 rows). This was a user-run analysis; no code or live app output was changed for the comparison.

## GPM rainfall candidate and window ablation (21 October 2026)

- Built `rainfall_gpm_2015_2026_to_2026-10-10_candidate.csv`: 20,140 reservoir-day rows for five lakes, 1 October 2015–10 October 2026, with no missing site values. IMD returned an empty response for 11 October; the range was kept complete through 10 October rather than imputing zero rainfall. The saved forecast origins end 30 September, so the candidate covers their antecedent windows.
- On identical baseline rows, the all-window (7/30/90-day) v2 comparison scored 444 development pairs and 10 recent pairs after excluding 93 disputed source pairs. Development pooled RMSE was 200.262 mcft without rain and 200.003 with all rainfall windows. Recent 10-pair RMSE was 194.056 without rain and 194.215 with all windows; no live update was made.
- Added candidate-only `--rainfall-windows` and `--development-only` controls to compare individual windows while keeping model settings, origins, labels, and conflict exclusions fixed. On the 444 development pairs, pooled RMSE was 200.262 with no rainfall, 198.523 with 7-day rainfall, 200.402 with 30-day rainfall, and 199.984 with 90-day rainfall. The 7-day candidate's MAE was 117.607 mcft versus 117.271 without rainfall.
- Scored the development-selected 7-day candidate once on the same 10 recent pairs: RMSE was 196.074 mcft versus 194.056 without rainfall, and MAE was 127.065 versus 125.174. The recent period had already been inspected for the combined-window experiment, so this is exploratory rather than a fresh independent holdout. Results are mixed by lake and do not establish a gain for the live combined model.
- Runs used XGBoost 3.4.1 with `AQUASAT_XGB_DEVICE=cuda`. Python syntax compilation and CLI help checks passed. The live forecast and dashboard files were not changed.

## Pooled v2 with Sentinel history (21 October 2026)

- Added an optional candidate-only `--satellite` path to the pooled v2 backtest. It reuses the same combined Sentinel-2-first/Sentinel-1-gap-fill OOS history and point-in-time feature definitions as the existing XGBoost baseline; the standard v2 run remains unchanged when the option is omitted.
- Compared both v2 variants on the same 454 forecast/actual pairs and same 93 disputed-pair exclusions. The 444 development pairs scored RMSE 200.262 mcft without satellite and 204.296 with satellite (MAE 117.271 vs 120.290). The 10 September–October 2026 pairs scored RMSE 194.056 without and 195.234 with satellite (MAE 125.174 vs 123.638). The recent block had been inspected in earlier work, so it is not a fresh independent holdout. The candidate does not justify changing the live forecast.
- Satellite features were checked against the live XGBoost calculations for one historical anchor per lake; all four values matched. The backtest also validated every target against the same conflict-filtered official readings.
- GPU run used XGBoost 3.4.1 with `AQUASAT_XGB_DEVICE=cuda`. Python syntax compilation and CLI help checks passed. Candidate outputs: `forecast_backtest_xgb_v2_satellite_candidate.csv` and `forecast_backtest_xgb_v2_satellite_summary_candidate.csv`. No live forecast, production model, or UI files were changed.

## Live merged-GPM rainfall integration (21 October 2026)

- Retrieved daily IMD/NCMRWF merged GPM grids for 11–20 October 2026 (50 site rows). The 21 October endpoint returned an empty response. The combined history now has 20,190 rows for five reservoirs over 4,038 complete dates, 1 October 2015–20 October 2026.
- The live 15-day XGBoost path now includes complete antecedent 7-, 30-, and 90-day GPM totals along with official-storage history and the existing S2-first/S1-fallback satellite lag. Features exclude rainfall on the origin date and all dates after it. For the current 16 October official origin, used rainfall ends 15 October.
- The pooled-v2 rain comparison is the saved quantitative comparison for the GPM feature; it did not include satellite features. Its recent 10-pair RMSE changed from 194.056 to 194.215 mcft (slightly higher), while MAE changed from 125.174 to 122.364 mcft. Do not present these figures as validation of the live combined model or as a broad accuracy gain.
- Until a longer combined-model backtest is available, the displayed empirical forecast range uses the prior S2/S1-only rolling-origin errors and labels that source explicitly. The range is approximate, not rainfall-model calibration.
