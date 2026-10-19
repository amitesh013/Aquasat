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
  forecast_origin?: string;
  horizon_days?: number;
  day15_prediction_mcft?: number;
  satellite_source?: string;
  satellite_feature_date?: string;
  satellite_age_days?: number;
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
