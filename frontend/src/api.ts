import type { DashboardData, RainfallCandidateResult } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export type AuthStatus = {
  required: boolean;
  configured: boolean;
  authenticated: boolean;
  email?: string | null;
  username?: string | null;
};

async function responseError(response: Response, fallback: string): Promise<Error> {
  let message = `${fallback} (${response.status})`;
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") message = payload.detail;
  } catch {
    // Keep the useful HTTP status when the response is not JSON.
  }
  return new Error(message);
}

export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await fetch(`${API_BASE}/api/v1/auth/status`, {
    headers: { Accept: "application/json" },
    credentials: "include",
  });
  if (!response.ok) throw await responseError(response, "Could not check sign-in status");
  return response.json() as Promise<AuthStatus>;
}

export async function createAccount(username: string, email: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/auth/register`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username, email, password }),
  });
  if (!response.ok) throw await responseError(response, "Could not create account");
}

export async function signIn(usernameOrEmail: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username_or_email: usernameOrEmail, password }),
  });
  if (!response.ok) throw await responseError(response, "Sign-in failed");
}

export async function signOut(): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/auth/logout`, {
    method: "POST",
    headers: { Accept: "application/json" },
    credentials: "include",
  });
  if (!response.ok) throw await responseError(response, "Could not sign out");
}

export async function getDashboard(signal?: AbortSignal): Promise<DashboardData> {
  const response = await fetch(`${API_BASE}/api/v1/dashboard`, {
    headers: { Accept: "application/json" },
    credentials: "include",
    signal,
  });
  if (!response.ok) {
    throw await responseError(response, "Dashboard API returned an error");
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
    credentials: "include",
    signal,
  });
  if (!response.ok) throw new Error(`Rainfall research API returned ${response.status}`);
  const result: unknown = await response.json();
  if (typeof result !== "object" || result === null || !("development" in result) || !("september_october_2026" in result)) {
    throw new Error("Rainfall research API returned an invalid result.");
  }
  return result as RainfallCandidateResult;
}
