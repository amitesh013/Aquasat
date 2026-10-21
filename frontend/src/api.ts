import type { DashboardData, RainfallCandidateResult } from "./types";

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

export async function getRainfallCandidate(signal?: AbortSignal): Promise<RainfallCandidateResult> {
  const response = await fetch(`${API_BASE}/api/v1/research/rainfall-candidate`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error(`Rainfall research API returned ${response.status}`);
  const result: unknown = await response.json();
  if (typeof result !== "object" || result === null || !("development" in result) || !("september_october_2026" in result)) {
    throw new Error("Rainfall research API returned an invalid result.");
  }
  return result as RainfallCandidateResult;
}
