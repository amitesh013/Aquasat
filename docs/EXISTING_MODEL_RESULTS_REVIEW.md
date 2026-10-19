# Review of included model result files

These values were read from the CSV outputs in the ZIP. They were not regenerated or independently validated. Treat them as archive results, not final acceptance evidence.

**Status update (19 October 2026):** the archive `metrics.csv` values below were superseded first by clear95 expanding metrics and then by active early-selected Sentinel-2 threshold metrics, scored on the later 20% holdout. See the current `metrics.csv`, `README.md`, and `AQUASAT_HANDOFF_GUIDE.md` for current scores. The original figures remain here only as a record of the ZIP's contents.

## Calibration metrics file

`metrics.csv` contains:

| Reservoir | Selected model in file | RMSE (mcft) | Bias (mcft) | Low-storage bias (mcft) | Matched count | Range restriction |
|---|---|---:|---:|---:|---:|---|
| Poondi | Gaussian Process | 419.1 | -4.2 | 19.9 | 77 | — |
| Cholavaram | Offset power-law | 136.9 | -12.9 | 14.5 | 85 | — |
| Puzhal | Offset power-law | 405.0 | 1.1 | 11.2 | 84 | — |
| Chembarambakkam | Offset power-law | 486.9 | 4.5 | 138.0 | 73 | — |
| Veeranam | Power-law | 126.9 | 3.0 | 82.1 | 22 | Fit limited to 600 mcft |

The active `prepare_data.py` computes repeated random-fold cross-validation metrics, not chronological holdout metrics. The original manual source is absent. The separate five `*_calibration_data.csv` files have zero populated `mcft_exact_date` values; they are not the labels behind these metric counts.

## Forecast experiment file

`forecast_baselines.csv` contains 24 origins per reservoir for four methods (Persistence, Anchored seasonal, XGBoost, Prophet). Values below summarize the saved `err` column as RMSE normalized by reservoir capacity. The script describes this as a one-month-ahead comparison over the final 24 monthly origins. It linearly interpolates up to two missing months before evaluation, so some scored targets may be interpolated rather than verified observations. The file has no horizon/date-pair metadata beyond monthly origin and actual values.

| Method | Poondi | Cholavaram | Puzhal | Chembarambakkam | Veeranam | Mean normalized RMSE |
|---|---:|---:|---:|---:|---:|---:|
| Persistence | 18.7% | 10.8% | 8.8% | 9.6% | 30.9% | 15.8% |
| Anchored seasonal | 15.1% | 9.5% | 8.4% | 7.6% | 26.8% | 13.5% |
| XGBoost | 15.1% | 11.0% | 8.3% | 9.3% | 26.4% | 14.0% |
| Prophet | 26.2% | 36.7% | 39.0% | 30.2% | 30.9% | 32.6% |

Each cell uses 24 saved pairs. This is a historical one-month forecast experiment, not evidence for the SRS's 7–15-day claim. It does not establish production model selection because target values may include interpolation. **Current implementation note (19 October 2026):** after this review, the user authorized a separate 15-day XGBoost live forecast from `generate_live_xgboost_forecast.py`. That newer forecast uses the rolling-origin pipeline described in the handoff; it does not make these older monthly results comparable or more conclusive.
