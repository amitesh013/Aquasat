# Bug log

| ID | Finding | Impact | Change / state |
|---|---|---|---|
| AQUA-01 | No `scrape_cmwssb.py` was included, despite a known risk of confusing current and prior-year storage. | Could ingest previous-year values as current. | Added parser selecting the exact `Storage (mcft)` header and validating displayed date; tested with fixture where previous-year value is larger. Live request not yet verified. |
| AQUA-02 | Existing project has no original or clean manually transcribed CSV, while `calibrate_all.py` expects `handwritten_ground_truth_clean.csv`. | Calibration cannot be reproduced and manual source preservation cannot be verified. | Documented blocker; no substitute data fabricated. |
| AQUA-03 | Calibration script uses random splitting and hardcoded model choice. | Validation may leak time structure and model selection evidence is weak. | Documented; requires source recovery before re-evaluation. |
| AQUA-04 | Production forecast CSV lacked model, generation time, origin, horizon and satellite-feature metadata. | Dashboard freshness and forecast provenance were hard to establish. | Resolved for current output: the live generator writes model, UTC generation time, origin, horizon, source/date/age, backtest RMSE, and pair-count metadata atomically. |
| AQUA-05 | Forecast generation dependencies were not included with the dashboard requirements. | Live forecast could not be regenerated from a standard app setup. | Resolved in `requirements.txt`: NumPy, scikit-learn, and XGBoost are declared with Streamlit dependencies. |
| AQUA-06 | Similar CSV names represent experiments, source subsets and generated outputs; no byte-identical duplicates found. | Blind cleanup risks removing necessary source lineage. | Inventory created; no CSV deleted. |
