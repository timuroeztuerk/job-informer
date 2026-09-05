import type {
  DbSummary,
  Job,
  JobFlag,
  JobDescriptionResponse,
  DescriptionQueueState,
  JobArchiveFilter,
  JobRelevanceFilter,
  IntelligenceWindow,
  JobStats,
  JobsResponse,
  RunRequest,
  RunStatus,
  RunSummary,
} from "./types";
import { buildApiUrl } from "./apiUrl";

export const API_BASE = import.meta.env.VITE_API_BASE || "/";
const API_TOKEN = import.meta.env.VITE_API_TOKEN;

const apiConfigurationHint = `Check that the backend is running and VITE_API_BASE (currently "${API_BASE}") is correct.`;

function authHeaders(extra?: Record<string, string>): HeadersInit {
  const headers: Record<string, string> = extra ? { ...extra } : {};
  if (API_TOKEN) {
    headers["x-api-token"] = API_TOKEN;
  }
  return headers;
}

function buildUrl(path: string, params?: Record<string, string | number | undefined>): string {
  return buildApiUrl(path, params, API_BASE, window.location.origin);
}

export function getApiErrorMessage(error: unknown, fallback = "Could not connect to the API."): string {
  if (error instanceof Error) {
    if (error.name === "TypeError" || /failed to fetch|networkerror|load failed/i.test(error.message)) {
      return `${fallback} ${apiConfigurationHint}`;
    }
    if (error.message.trim()) return error.message;
  }
  return `${fallback} ${apiConfigurationHint}`;
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (error) {
    throw new Error(getApiErrorMessage(error));
  }
}

async function responseError(res: Response): Promise<Error> {
  const contentType = res.headers.get("content-type")?.toLowerCase() || "";
  const text = await res.text();

  if (contentType.includes("text/html") || /^\s*<!doctype html|^\s*<html/i.test(text)) {
    return new Error(`API returned HTML instead of JSON (status ${res.status}). ${apiConfigurationHint}`);
  }

  if (res.status === 401 || res.status === 403) {
    return new Error(`API access was rejected (status ${res.status}). Check VITE_API_TOKEN and VITE_API_BASE (currently "${API_BASE}").`);
  }

  if (contentType.includes("json") && text) {
    try {
      const payload = JSON.parse(text) as { detail?: unknown; error?: unknown; message?: unknown };
      const message = payload.detail ?? payload.error ?? payload.message;
      if (typeof message === "string" && message.trim()) {
        return new Error(`API request failed (status ${res.status}): ${message}`);
      }
    } catch {
      // Fall back to the response text below when an error payload is malformed.
    }
  }

  return new Error(text.trim() || `API request failed with status ${res.status}.`);
}

async function handleJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw await responseError(res);
  }

  const contentType = res.headers.get("content-type")?.toLowerCase() || "";
  if (!contentType.includes("json")) {
    throw new Error(`API returned ${contentType || "an unknown content type"} instead of JSON. ${apiConfigurationHint}`);
  }

  try {
    return JSON.parse(await res.text()) as T;
  } catch {
    throw new Error(`API returned invalid JSON. ${apiConfigurationHint}`);
  }
}

export async function startRun(payload: RunRequest): Promise<RunStatus> {
  const res = await apiFetch(buildUrl("runs"), {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return handleJson<RunStatus>(res);
}

export async function getRunStatus(runId: string): Promise<RunStatus> {
  const res = await apiFetch(buildUrl(`runs/${runId}`), { headers: authHeaders() });
  return handleJson<RunStatus>(res);
}

export async function listRuns(limit = 20): Promise<RunSummary[]> {
  const res = await apiFetch(buildUrl("runs", { limit }), { headers: authHeaders() });
  return handleJson<RunSummary[]>(res);
}

export interface JobsQuery {
  limit?: number;
  offset?: number;
  search?: string;
  location?: string;
  source?: string;
  company?: string;
  companyExact?: boolean;
  locationPrimary?: boolean;
  lastSeenFrom?: string;
  repeated?: boolean;
  roleFamily?: string;
  queryGroup?: string;
  relevanceOutcome?: JobRelevanceFilter;
  favorite?: boolean;
  flagged?: boolean;
  archived?: JobArchiveFilter;
  dateFrom?: string;
  dateTo?: string;
  sort?:
    | "scraped_at_desc"
    | "scraped_at_asc"
    | "last_seen_desc"
    | "last_seen_asc"
    | "seen_count_desc"
    | "seen_count_asc"
    | "title_asc"
    | "title_desc";
}

function normalizeDateStart(value?: string): string | undefined {
  if (!value) return undefined;
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00` : value;
}

function normalizeDateEnd(value?: string): string | undefined {
  if (!value) return undefined;
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T23:59:59` : value;
}

export async function fetchJobs(query: JobsQuery = {}): Promise<JobsResponse> {
  const res = await apiFetch(
    buildUrl("jobs", {
      limit: query.limit ?? 25,
      offset: query.offset ?? 0,
      search: query.search,
      location: query.location,
      source: query.source,
      company: query.company,
      company_exact: query.companyExact ? "true" : undefined,
      location_primary: query.locationPrimary ? "true" : undefined,
      last_seen_from: normalizeDateStart(query.lastSeenFrom),
      repeated: query.repeated ? "true" : undefined,
      role_family: query.roleFamily,
      query_group: query.queryGroup,
      relevance_outcome: query.relevanceOutcome,
      favorite: query.favorite ? "true" : undefined,
      flagged: query.flagged ? "true" : undefined,
      archived: query.archived,
      date_from: normalizeDateStart(query.dateFrom),
      date_to: normalizeDateEnd(query.dateTo),
      sort: query.sort,
    }),
    { headers: authHeaders() }
  );
  return handleJson<JobsResponse>(res);
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), { headers: authHeaders() });
  return handleJson<Job>(res);
}

export async function archiveJob(jobId: string): Promise<void> {
  const res = await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
}

export async function restoreJob(jobId: string): Promise<void> {
  const res = await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/restore`), {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw await responseError(res);
  }
}

export async function setJobFlag(jobId: string, isFlagged: boolean, reason?: string): Promise<JobFlag> {
  const res = await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/flag`), {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ is_flagged: isFlagged, flag_reason: reason }),
  });
  return handleJson<JobFlag>(res);
}

export async function setJobFavorite(
  jobId: string,
  isFavorite: boolean
): Promise<Pick<Job, "job_id" | "is_favorite">> {
  const res = await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/favorite`), {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ is_favorite: isFavorite }),
  });
  return handleJson<Pick<Job, "job_id" | "is_favorite">>(res);
}

export async function fetchStats(): Promise<JobStats> {
  const res = await apiFetch(buildUrl("stats"), { headers: authHeaders() });
  return handleJson<JobStats>(res);
}

export async function fetchDbSummary(window: IntelligenceWindow = "all"): Promise<DbSummary> {
  const res = await apiFetch(buildUrl("db-summary", { window }), { headers: authHeaders() });
  return handleJson<DbSummary>(res);
}

export async function fetchDescription(jobId: string): Promise<JobDescriptionResponse> {
  return handleJson(await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/description`), { headers: authHeaders() }));
}

export async function fetchAIQueue(): Promise<import("./types").AIQueueState> {
  return handleJson(await apiFetch(buildUrl("ai/queue"), { headers: authHeaders() }));
}

export async function queueAI(retry = false): Promise<import("./types").AIQueueState> {
  return handleJson(await apiFetch(buildUrl("ai/queue"), {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ retry }),
  }));
}

export async function controlAIQueue(action: "pause" | "resume"): Promise<import("./types").AIQueueState> {
  return handleJson(await apiFetch(buildUrl(`ai/queue/${action}`), { method: "POST", headers: authHeaders() }));
}

export async function fetchJobExtraction(jobId: string): Promise<import("./types").AIJobResult> {
  return handleJson(await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/extraction`), { headers: authHeaders() }));
}

export async function requestDescription(jobId: string, refresh = false): Promise<JobDescriptionResponse> {
  return handleJson(await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/description`), {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ refresh }),
  }));
}

export async function reparseDescription(jobId: string): Promise<JobDescriptionResponse> {
  return handleJson(await apiFetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/description/reparse`), { method: "POST", headers: authHeaders() }));
}

export async function fetchDescriptionQueue(): Promise<DescriptionQueueState> {
  return handleJson(await apiFetch(buildUrl("descriptions/queue"), { headers: authHeaders() }));
}

export async function queueDescriptions(selection: "all" | "retry" = "all"): Promise<DescriptionQueueState> {
  return handleJson(await apiFetch(buildUrl("descriptions/queue"), {
    method: "POST", headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(selection === "all" ? { selection } : { selection, limit: 10 }),
  }));
}

export async function controlDescriptionQueue(action: "pause" | "resume"): Promise<DescriptionQueueState> {
  return handleJson(await apiFetch(buildUrl(`descriptions/queue/${action}`), { method: "POST", headers: authHeaders() }));
}
