import type { DashboardData } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export async function getDashboard(signal?: AbortSignal): Promise<DashboardData> {
  const response = await fetch(`${API_BASE}/api/v1/dashboard`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    let message = `Dashboard API returned ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
    } catch {
      // Keep the useful HTTP status when the response is not JSON.
    }
    throw new Error(message);
  }
  const payload: unknown = await response.json();
  if (typeof payload !== "object" || payload === null) {
    throw new Error("Dashboard API returned an invalid response.");
  }
  const dashboard = payload as Partial<DashboardData>;
  if (!Array.isArray(dashboard.history) || !Array.isArray(dashboard.forecasts) ||
      typeof dashboard.metrics !== "object" || dashboard.metrics === null ||
      typeof dashboard.data_status !== "object" || dashboard.data_status === null ||
      typeof dashboard.data_status.database !== "string") {
    throw new Error("Dashboard API response is missing history, forecasts, metrics, or database status.");
  }
  return dashboard as DashboardData;
}
