# Pooled XGBoost v2 candidate

Update (21 October 2026): at the user's request, the live lake-specific 15-day XGBoost forecast now includes antecedent 7-, 30-, and 90-day totals from the merged IMD/NCMRWF GPM series, alongside the existing official-storage and S2-first/S1-fallback satellite features. The pooled-v2 results below remain separate research comparisons and do not prove that rainfall improves the live combined model.

## Optional NVIDIA GPU use in VS Code

The baseline and v2 candidate both default to CPU. To use an NVIDIA GPU, open a PowerShell terminal in VS Code, move to this repository folder, and install/update XGBoost in the same Python environment selected by VS Code:

```powershell
python -m pip install --upgrade xgboost
python -c "import sys, xgboost; print(sys.executable); print('XGBoost', xgboost.__version__)"
$env:AQUASAT_XGB_DEVICE = "cuda"
```

Before running a long backtest, check that this Python environment can train a tiny model on the GPU:

```powershell
python -c "import numpy as np; from xgboost import XGBRegressor; XGBRegressor(tree_method='hist', device='cuda', n_estimators=2).fit(np.array([[0.0],[1.0],[2.0],[3.0]]), np.array([0.0,1.0,2.0,3.0])); print('CUDA training succeeded')"
```

Keep that VS Code terminal open and run the backtest commands below in it; the environment variable makes both XGBoost implementations use CUDA. The change affects where training runs, not which features or model settings are scored. To return to CPU, run `$env:AQUASAT_XGB_DEVICE = "cpu"` or open a fresh terminal. XGBoost 2.0 or newer is needed for its `device` option. See the [XGBoost GPU guide](https://xgboost.readthedocs.io/en/stable/gpu/) and [installation guide](https://xgboost.readthedocs.io/en/stable/install.html).

These files preserve Claude's pooled five-lake XGBoost approach as a research candidate:

- `aquasat_forecast_v2_candidate.py` builds normalized storage-history, cross-lake, season, and optional antecedent-rainfall features.
- `backtest_xgboost_v2_candidate.py` compares it with saved baseline forecasts on identical target rows and checks those actuals against conflict-filtered official storage.

The v2 settings are frozen to the reported configuration: 350 trees, depth 4, learning rate 0.03, five-year recency half-life, and the same regularization parameters. Quarterly fits are grouped by forecast cutoff quarter and use labels strictly before the first forecast origin in that quarter. The candidate does not update the live dashboard or forecast.

## Main-model comparison and recent holdout

The saved backtest covers 2019 through August 2026. Claude selected settings after looking at that period, so those rows are development results, not independent validation. The project data currently has official storage through 16 October 2026; the September and October monthly forecast targets are the only available post-tuning holdout and amount to very few pairs.

First generate the two new monthly baseline forecasts, using the existing S2-first/S1-fallback feature history:

```powershell
python backtest_15day_forecasts.py `
  --ground-truth reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv `
  --satellite sentinel2_sentinel1_oos_storage_candidate.csv `
  --conflicts ground_truth_conflicts_for_review.csv `
  --skip-prophet `
  --start-date 2026-09-01 `
  --end-date 2026-10-01 `
  --output forecast_backtest_sep_oct_2026_baseline_candidate.csv
```

Then score the candidate against the existing 444-pair baseline and those new holdout rows:

```powershell
python backtest_xgboost_v2_candidate.py `
  --baseline forecast_backtest_s2_s1_candidate.csv forecast_backtest_sep_oct_2026_baseline_candidate.csv `
  --holdout-start 2026-09-01 `
  --predictions-output forecast_backtest_xgb_v2_candidate.csv `
  --summary-output forecast_backtest_xgb_v2_summary_candidate.csv
```

The summary reports the old storage-only XGBoost, old satellite-assisted XGBoost, and v2 on the same pairs. Treat the 2019–August 2026 v2 score as reproduction/development evidence because those dates were used during Claude's tuning. Treat the September–October score as the locked check, but do not overinterpret it because its sample is small.

## Satellite-augmented v2 candidate (21 October 2026)

The candidate backtest also accepts `--satellite` to test the same Sentinel-2-first/Sentinel-1-gap-fill OOS history used by the saved baseline. At each training anchor and forecast origin it uses the latest satellite estimate on or before that date, plus the four satellite inputs used by the existing XGBoost (`available`, storage fraction, difference from the latest official reading, and age). The forecast labels, target readings, quarterly fit origins, and conflict-filtered official data remain unchanged.

Run the paired comparison with:

```powershell
python backtest_xgboost_v2_candidate.py `
  --baseline forecast_backtest_s2_s1_candidate.csv forecast_backtest_sep_oct_2026_baseline_candidate.csv `
  --satellite sentinel2_sentinel1_oos_storage_candidate.csv `
  --holdout-start 2026-09-01 `
  --predictions-output forecast_backtest_xgb_v2_satellite_candidate.csv `
  --summary-output forecast_backtest_xgb_v2_satellite_summary_candidate.csv
```

On 454 shared forecast/actual pairs, v2 without satellite scored 200.127 mcft RMSE / 117.445 MAE; v2 with satellite scored 204.100 / 120.364. On the 444 development pairs, RMSE rose from 200.262 to 204.296. On the 10 September–October pairs, RMSE was 194.056 without satellite and 195.234 with it; MAE was 125.174 and 123.638 respectively. That recent block was previously inspected in earlier experiments, so it is not a fresh independent holdout. The satellite-augmented candidate does not support a live model change.

The satellite feature calculations were checked against the existing live XGBoost feature definitions at one point-in-time origin per lake. All four values matched. The backtest excluded the same 93 disputed source pairs and validated all 454 target readings against the conflict-filtered official file. Both output files are research candidates; the live forecast and UI were not changed.

## Separate storage-only rainfall candidate

The earlier 7-day rainfall ablation compared pooled v2 with and without rainfall and used no Sentinel features. It did not test the original storage-only XGBoost. `backtest_15day_forecasts.py` accepts `--no-satellite --rainfall` for storage-only rainfall comparisons, or `--satellite ... --rainfall ...` to score the combined satellite-plus-rainfall model on the same rolling-origin dates and official readings. These remain research backtests; the live forecast now uses rainfall at the user's request.

The GPM candidate currently ends on 10 October 2026, so use the 1 October cutoff (forecast origin 30 September), where the antecedent windows are complete:

```powershell
$env:AQUASAT_XGB_DEVICE = 'cuda'; python backtest_15day_forecasts.py --ground-truth reviewed_ground_truth_2016_2026_veeranam_merged_candidate.csv --monthly chennai_lakes_combined.csv --conflicts ground_truth_conflicts_for_review.csv --no-satellite --rainfall rainfall_gpm_2015_2026_to_2026-10-10_candidate.csv --rainfall-windows 7 --start-date 2019-01-01 --end-date 2026-10-01 --skip-prophet --output forecast_backtest_storage_only_rain7_candidate.csv
```

Compare `xgboost_rainfall` with `xgboost` in the same output. Require a gain on the later dates before considering the rainfall feature for a live forecast.

## Rainfall comparison and data limit

The official IMD 0.25-degree daily NetCDF page describes its standard archive as 1901–2024, not through 2026: <https://imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html>. The current `build_imd_rainfall.py` correctly rejects years after 2024. Do not relabel another rainfall product as this archive. Extending the same series to 2025–2026 needs a verified compatible source or a separate source/method audit.

### Separate IMD/NCMRWF merged GPM rainfall candidate

The separate [IMD/NCMRWF daily merged GPM page](https://imdpune.gov.in/cmpg/Realtimedata/gpm/Rain_Download.html) serves one 0.25-degree, 241-by-281 binary rainfall grid for a chosen date. The official [dataset paper](https://link.springer.com/article/10.1007/s12040-025-02550-0) describes it as 24-hour accumulated merged gauge and GPM rainfall, available on IMD Pune's site on a near-real-time basis since 1 October 2015. The page’s sample control file specifies 241 longitude cells from 50.0°E at 0.25° spacing, 281 latitude cells from 30.0°S at 0.25° spacing, and −999 as the undefined value.

The date-specific binary endpoint was checked on 1 October 2015 and 15 October 2019, 2025, and 2026. All four downloads had the expected 270,884 bytes (241 × 281 × 4) and decoded to five valid rainfall values. The annual merged NetCDF selector lists 2016–2022, but its 2022 download returned a zero-byte file in this check, so the candidate builder uses the verified daily endpoint instead. A complete 2015–2026 build therefore makes roughly 4,000 daily requests (about 1.1 GB of raw grids); it checkpoints a small partial CSV and resumes if interrupted. Run the one-day check first:

```powershell
python build_imd_gpm_rainfall_candidate.py `
  --start-date 2026-10-15 `
  --end-date 2026-10-15 `
  --output rainfall_gpm_smoke_candidate.csv
```

Then build the separate history. Keep the exact same command if it needs to resume from `rainfall_gpm_2015_2026_candidate.csv.partial`:

```powershell
python build_imd_gpm_rainfall_candidate.py `
  --start-date 2015-10-01 `
  --end-date 2026-10-20 `
  --output rainfall_gpm_2015_2026_candidate.csv
```

The output keeps the same `date`, `reservoir`, and `rainfall_mm` columns the v2 candidate expects, plus source and grid-cell provenance. Rainfall is sampled at the nearest grid cell to each lake centre, not averaged over a catchment. Keep this GPM/gauge candidate separate from `rainfall_reservoir_daily_2016_2024.csv`; the two rainfall products are not interchangeable.

After the full candidate is present, compare it on the existing baseline rows and locked September–October 2026 holdout. This fits and scores the frozen v2 with and without 7-, 30-, and 90-day antecedent GPM rainfall, while rechecking the same official actuals and dispute exclusions:

```powershell
python backtest_xgboost_v2_candidate.py `
  --baseline forecast_backtest_s2_s1_candidate.csv forecast_backtest_sep_oct_2026_baseline_candidate.csv `
  --holdout-start 2026-09-01 `
  --rainfall rainfall_gpm_2015_2026_candidate.csv `
  --predictions-output forecast_backtest_xgb_v2_gpm_candidate.csv `
  --summary-output forecast_backtest_xgb_v2_gpm_summary_candidate.csv
```

This pooled-v2 comparison does not edit the live forecast. The live model now uses rainfall at the user's request; this comparison is satellite-free and its ten-pair recent block is too small and mixed to establish an accuracy gain for the live combined model.

### Exploratory rainfall-window ablation (21 October 2026)

`backtest_xgboost_v2_candidate.py` also accepts `--rainfall-windows` and `--development-only`. These options keep the Claude v2 model settings fixed while testing selected antecedent windows. `--development-only` excludes every target on or after `--holdout-start`, so rainfall windows are compared on the 444 development pairs without selecting from the September–October targets. Example:

```powershell
python backtest_xgboost_v2_candidate.py `
  --baseline forecast_backtest_s2_s1_candidate.csv forecast_backtest_sep_oct_2026_baseline_candidate.csv `
  --holdout-start 2026-09-01 `
  --development-only `
  --rainfall rainfall_gpm_2015_2026_to_2026-10-10_candidate.csv `
  --rainfall-windows 7 `
  --predictions-output forecast_backtest_xgb_v2_gpm_7d_dev_candidate.csv `
  --summary-output forecast_backtest_xgb_v2_gpm_7d_dev_summary_candidate.csv
```

Use `--rainfall-windows 30` or `--rainfall-windows 90` for the other single-window candidates. The 21 October experiment found 7-day rainfall had the lowest development RMSE of the single-window variants, but its recent 10-pair check was worse than v2 without rainfall and the check had already been inspected for the combined-window candidate. Treat all three window results as exploratory; they do not establish a live-model improvement.

The workspace already has `rainfall_reservoir_daily_2016_2024.csv`, with complete daily rows for all five lakes from 1 January 2016 through 31 December 2024. If it needs to be rebuilt, this command downloads any missing annual NetCDF files:

```powershell
python build_imd_rainfall.py `
  --start-year 2016 `
  --end-year 2024 `
  --output rainfall_reservoir_daily_2016_2024_candidate.csv
```

Compare the frozen v2 model with and without antecedent rainfall on the 2019–2024 rows. This is a historical feature comparison, not a fresh untouched holdout, because Claude's settings were selected using the broader 2019–2026 backtest:

```powershell
python backtest_xgboost_v2_candidate.py `
  --baseline forecast_backtest_s2_s1_candidate.csv `
  --start-date 2019-01-01 `
  --end-date 2024-12-31 `
  --rainfall rainfall_reservoir_daily_2016_2024.csv `
  --predictions-output forecast_backtest_xgb_v2_rainfall_2019_2024_candidate.csv `
  --summary-output forecast_backtest_xgb_v2_rainfall_2019_2024_summary_candidate.csv
```

The script requires complete daily rainfall for all selected windows at every scored origin; missing dates are not treated as dry days. The live model currently uses 7-, 30-, and 90-day windows. Its saved pooled-v2 comparison is mixed and satellite-free, so do not describe it as validation that the live combined model improves accuracy.
