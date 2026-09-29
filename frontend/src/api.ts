import type { Insight, LlmTrace, MetricsReport, SignalsReport, TrackedRepo } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export interface WindowParams { from: string; to: string }
const qs = (w: WindowParams) => `?from=${encodeURIComponent(w.from)}&to=${encodeURIComponent(w.to)}`;
const repoPath = (owner: string, name: string) => `/api/v1/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;

export const api = {
  listRepos: () => request<TrackedRepo[]>("/api/v1/repos"),
  trackRepo: (owner: string, name: string) => request<TrackedRepo>("/api/v1/repos", { method: "POST", body: JSON.stringify({ owner, name }) }),
  syncStatus: (owner: string, name: string) => request<TrackedRepo>(`${repoPath(owner, name)}/sync`),
  triggerSync: (owner: string, name: string) => request<TrackedRepo>(`${repoPath(owner, name)}/sync`, { method: "POST" }),
  metrics: (owner: string, name: string, w: WindowParams) => request<MetricsReport>(`${repoPath(owner, name)}/metrics${qs(w)}`),
  signals: (owner: string, name: string, w: WindowParams) => request<SignalsReport>(`${repoPath(owner, name)}/signals${qs(w)}`),
  insight: (owner: string, name: string, w: WindowParams, refresh = false) =>
    request<Insight>(`${repoPath(owner, name)}/insights${qs(w)}${refresh ? "&refresh=true" : ""}`, { method: "POST" }),
  traces: () => request<LlmTrace[]>("/api/v1/llm/traces?limit=20"),
};
