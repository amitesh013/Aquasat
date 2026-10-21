export type HistoryRow = {
  reservoir: string;
  date: string;
  official_mcft: number | null;
  sat_estimate_mcft: number | null;
};

export type ForecastRow = {
  ds: string;
  reservoir: string;
  yhat: number;
  yhat_lower: number | null;
  yhat_upper: number | null;
  model?: string;
  generated_at_utc?: string;
  forecast_origin?: string;
  horizon_days?: number;
  day15_prediction_mcft?: number;
  satellite_source?: string;
  satellite_feature_date?: string;
  satellite_age_days?: number;
  rainfall_source?: string;
  rainfall_window_days?: string;
  rainfall_feature_through_date?: string;
  rainfall_7d_mm?: number;
  rainfall_30d_mm?: number;
  rainfall_90d_mm?: number;
  interval_method?: string;
};

export type MetricRow = Record<string, string | number | null>;

export type DashboardData = {
  history: HistoryRow[];
  forecasts: ForecastRow[];
  metrics: Record<string, MetricRow>;
  data_status: {
    database: string;
    last_synced_at_utc: string | null;
    sources?: { source_file: string; row_count: number; synced_at_utc: string }[];
  };
};

export type RainfallCandidateResult = {
  title: string;
  status: string;
  forecast_horizon_days: number;
  rainfall_windows_days: number[];
  rainfall_source: string;
  rainfall_first_date: string;
  rainfall_last_date: string;
  rainfall_sampling: string;
  live_rainfall_last_date?: string;
  live_model?: string;
  model: string;
  satellite_features_used: boolean;
  paired_forecasts: number;
  disputed_source_pairs: number;
  all_pairs: {
    pairs: number;
    baseline_rmse_mcft: number;
    rainfall_rmse_mcft: number;
    baseline_rmse_pct_capacity: number;
    rainfall_rmse_pct_capacity: number;
    baseline_mae_mcft: number;
    rainfall_mae_mcft: number;
  };
  development: {
    pairs: number;
    baseline_rmse_mcft: number;
    rainfall_rmse_mcft: number;
    baseline_rmse_pct_capacity: number;
    rainfall_rmse_pct_capacity: number;
    baseline_mae_mcft: number;
    rainfall_mae_mcft: number;
  };
  september_october_2026: {
    pairs: number;
    target_start_date: string;
    baseline_rmse_mcft: number;
    rainfall_rmse_mcft: number;
    baseline_rmse_pct_capacity: number;
    rainfall_rmse_pct_capacity: number;
    baseline_mae_mcft: number;
    rainfall_mae_mcft: number;
  };
  forecast_model_includes_rainfall: boolean;
  source_summary_file: string;
};
