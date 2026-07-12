import type {
  DbSummary,
  Job,
  JobAnnotation,
  JobArchiveFilter,
  JobAnnotationPriority,
  JobAnnotationStatus,
  JobStats,
  JobsResponse,
  RunRequest,
  RunStatus,
  RunSummary,
} from "./types";
import { buildApiUrl } from "./apiUrl";

export const API_BASE = import.meta.env.VITE_API_BASE || "/";
const API_TOKEN = import.meta.env.VITE_API_TOKEN;

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

async function handleJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed with status ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function startRun(payload: RunRequest): Promise<RunStatus> {
  const res = await fetch(buildUrl("runs"), {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return handleJson<RunStatus>(res);
}

export async function getRunStatus(runId: string): Promise<RunStatus> {
  const res = await fetch(buildUrl(`runs/${runId}`), { headers: authHeaders() });
  return handleJson<RunStatus>(res);
}

export async function listRuns(limit = 20): Promise<RunSummary[]> {
  const res = await fetch(buildUrl("runs", { limit }), { headers: authHeaders() });
  return handleJson<RunSummary[]>(res);
}

export interface JobsQuery {
  limit?: number;
  offset?: number;
  search?: string;
  location?: string;
  source?: string;
  company?: string;
  archived?: JobArchiveFilter;
  annotationStatus?: JobAnnotationStatus | "";
  annotationPriority?: JobAnnotationPriority | "";
  dateFrom?: string;
  dateTo?: string;
  sort?:
    | "scraped_at_desc"
    | "scraped_at_asc"
    | "last_seen_desc"
    | "last_seen_asc"
    | "seen_count_desc"
    | "seen_count_asc"
    | "fit_score_desc"
    | "fit_score_asc"
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
  const res = await fetch(
    buildUrl("jobs", {
      limit: query.limit ?? 25,
      offset: query.offset ?? 0,
      search: query.search,
      location: query.location,
      source: query.source,
      company: query.company,
      archived: query.archived,
      annotation_status: query.annotationStatus,
      annotation_priority: query.annotationPriority,
      date_from: normalizeDateStart(query.dateFrom),
      date_to: normalizeDateEnd(query.dateTo),
      sort: query.sort,
    }),
    { headers: authHeaders() }
  );
  return handleJson<JobsResponse>(res);
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), { headers: authHeaders() });
  return handleJson<Job>(res);
}

export async function archiveJob(jobId: string): Promise<void> {
  const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Archive failed with status ${res.status}`);
  }
}

export async function restoreJob(jobId: string): Promise<void> {
  const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/restore`), {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Restore failed with status ${res.status}`);
  }
}

export async function updateJobAnnotation(jobId: string, payload: JobAnnotation): Promise<JobAnnotation> {
  const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}/annotation`), {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  return handleJson<JobAnnotation>(res);
}

export async function fetchStats(): Promise<JobStats> {
  const res = await fetch(buildUrl("stats"), { headers: authHeaders() });
  return handleJson<JobStats>(res);
}

export async function fetchDbSummary(): Promise<DbSummary> {
  const res = await fetch(buildUrl("db-summary"), { headers: authHeaders() });
  return handleJson<DbSummary>(res);
}
