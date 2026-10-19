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
  return response.json() as Promise<DashboardData>;
}
