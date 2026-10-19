# Five-minute AquaSat demo script

This script reflects the supplied implementation. Use a local environment with Streamlit and Plotly installed. Do not describe the included records as live.

1. **Objective (30 sec):** AquaSat explores reservoir surface area from satellite imagery and links it to storage observations for Chennai's five reservoirs.
2. **Overview (45 sec):** Open the Streamlit dashboard and point out each reservoir, its latest available record date, source, and any stale-data notice. The archived dashboard data end on 18 September 2026.
3. **Reservoir comparison (60 sec):** Select a reservoir and compare satellite-derived estimates with the available official or historical record. Explain that same-month historical values and exact-date official observations have different time precision.
4. **Calibration (60 sec):** Show the available calibration metrics as experimental archive outputs. Explain that final metrics cannot be reproduced from this ZIP because the clean manual source file is missing, and that the existing calibration script uses a random split.
5. **Forecast (60 sec):** Show the 15-day XGBoost forecast. It predicts the day-15 endpoint from official storage history and the latest prior calibrated satellite-storage feature, with S2 first and S1 filling only same-date gaps. Explain that the plotted daily path is interpolated, the shaded residual range is approximate rather than a calibrated probability interval, and the 444-pair backtest did not show a broad improvement from S1.
6. **Quality warning (45 sec):** Show staleness and explain the Cholavaram saturation / image coverage review needs. The CMWSSB scraper tests verify current versus prior-year columns offline; live submission remains unverified.
7. **Close (40 sec):** Summarize the research demonstration and limitations: missing source file, unverified live scrape, satellite update ending September 2026, and the need for chronological validation. Use real screenshots only after launching the app; none are fabricated in this archive.

## Screenshot checklist

- Overview at desktop width with the stale-data message visible.
- Reservoir explorer with dates and units visible.
- Forecast page showing the selected method and input date.
- Data quality/methodology view showing the actual limitations.
