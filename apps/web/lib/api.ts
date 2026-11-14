import type {
  Analysis,
  AnalysisResults,
  AssistantAnswer,
  CandidateDetail,
  Comparison,
  Dataset,
  DatasetAvailability,
  JobEvent,
  Planet,
  Region,
  Report,
  ResultSummary,
  TerrainMesh,
} from "./types";

export const publicApiBase = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

const serverApiBase = (
  process.env.TERRAX_API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message: unknown }).message)
          : `Request failed with HTTP ${status}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  base = publicApiBase,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${base}/api/v1${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(init.headers || {}),
      },
      cache: init.cache ?? "no-store",
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "network error";
    throw new ApiError(
      0,
      `Cannot reach the SNSX SPACE API at ${base}. Check that the backend is running and that this page's origin is allowed by CORS. (${reason})`,
    );
  }
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? body;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  planets: (base = publicApiBase) => request<Planet[]>("/planets", {}, base),
  datasets: (params = "", base = publicApiBase) =>
    request<{ items: Dataset[]; total: number }>(`/datasets${params}`, {}, base),
  regions: (params = "", base = publicApiBase) =>
    request<{ items: Region[]; total: number } | Region[]>(`/regions${params}`, {}, base),
  analyses: (params = "", base = publicApiBase) =>
    request<{ items: Analysis[]; total: number }>(`/analyses${params}`, {}, base),
  analysis: (id: string, base = publicApiBase) =>
    request<Analysis>(`/analyses/${id}`, {}, base),
  results: (id: string, base = publicApiBase) =>
    request<AnalysisResults>(`/analyses/${id}/results`, {}, base),
  summary: (id: string, base = publicApiBase) =>
    request<{ analysis_id: string; status: string; result_summary: ResultSummary | null; error_message: string | null }>(
      `/analyses/${id}/result-summary`,
      {},
      base,
    ),
  candidate: (id: string, base = publicApiBase) =>
    request<CandidateDetail>(`/candidates/${id}`, {}, base),
  events: (id: string, base = publicApiBase) =>
    request<{ items: JobEvent[] }>(`/analyses/${id}/event-log`, {}, base),
  createAnalysis: (payload: unknown) =>
    request<Analysis>("/analyses", { method: "POST", body: JSON.stringify(payload) }),
  retryAnalysis: (id: string) => request<Analysis>(`/analyses/${id}/retry`, { method: "POST" }),
  comparison: (payload: unknown) =>
    request<Comparison>("/comparisons", { method: "POST", body: JSON.stringify(payload) }),
  assistant: (payload: unknown) =>
    request<AssistantAnswer>("/assistant/query", { method: "POST", body: JSON.stringify(payload) }),
  reports: (analysisId: string) =>
    request<Report[]>(`/reports?analysis_id=${encodeURIComponent(analysisId)}`),
  createReport: (analysisId: string, format: Report["format"]) =>
    request<Report>("/reports", {
      method: "POST",
      body: JSON.stringify({ analysis_id: analysisId, format }),
    }),
  report: (id: string) => request<Report>(`/reports/${id}`),
  probe: (bodyId?: string, limit = 8) =>
    request<{ datasets: DatasetAvailability[]; checked_at: string; available: number; requires_credentials: number; unavailable: number }>(
      `/datasets/probe?limit=${limit}${bodyId ? `&body_id=${encodeURIComponent(bodyId)}` : ""}`,
      { method: "POST" },
    ),
  health: (base = publicApiBase) =>
    request<{ status: string; database: string; worker_queue: { counts: Record<string, number>; queued: number; running: number; entity_counts?: Record<string, number> }; earthdata_configured: boolean; meteomatics_configured: boolean; llm_configured: boolean }>("/health", {}, base),
  terrain: (
    body: string,
    lon: number,
    lat: number,
    sizeKm: number,
    signal?: AbortSignal,
    resolution = 500,
  ) =>
    request<TerrainMesh>(
      `/terrain/${encodeURIComponent(body)}/${lon.toFixed(5)}/${lat.toFixed(5)}/${sizeKm.toFixed(1)}.json?resolution_m=${resolution}`,
      { signal },
    ),
  server: {
    planets: () => api.planets(serverApiBase),
    regions: (params = "") => api.regions(params, serverApiBase),
    datasets: (params = "") => api.datasets(params, serverApiBase),
    analyses: (params = "") => api.analyses(params, serverApiBase),
    analysis: (id: string) => api.analysis(id, serverApiBase),
    results: (id: string) => api.results(id, serverApiBase),
    summary: (id: string) => api.summary(id, serverApiBase),
    health: () => api.health(serverApiBase),
  },
};

export function textureUrl(body: string): string {
  return `${publicApiBase}/api/v1/textures/${body}.jpg`;
}

export function apiAssetUrl(path: string): string {
  return path.startsWith("http") ? path : `${publicApiBase}${path}`;
}

export function reportDownloadUrl(id: string): string {
  return `${publicApiBase}/api/v1/reports/${id}/download`;
}

export function eventSourceUrl(analysisId: string): string {
  return `${publicApiBase}/api/v1/analyses/${analysisId}/events`;
}

export function previewUrl(body: string, lon: number, lat: number, sizeKm: number): string {
  return `${publicApiBase}/api/v1/previews/${body}/${lon}/${lat}/${sizeKm}.jpg`;
}
