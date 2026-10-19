# Test plan

## Offline scraper tests

Run from this project directory:

```powershell
python -m unittest discover -s tests -v
```

The deterministic suite checks exact current-column selection (including a larger previous-year value), all five reservoir names and the Red Hills alias, missing rows, unknown table/header, mismatched displayed date, live form discovery, thousands separators and missing markers, capacity bounds, normalized duplicate dates, manual record preservation, blank-field merging, conflict reporting, atomic output, all-failed-run protection, dry run, and network error handling.

## Additional validation to perform when inputs become available

- Integration test scraper output through the actual ground-truth merge and calibration scripts.
- Verify scraper form parameters and response parsing against a real CMWSSB response, without updating the manual source.
- Rebuild calibration outputs from the original manual source, then validate exact-date provenance and chronological holdouts.
- Run forecasting comparison with identical rolling origins/horizons and verified actuals.
- Launch Streamlit and check all views at desktop and mobile widths.
- Rerun Earth Engine extraction only after credentials, geofences and cloud-mask review are available.
