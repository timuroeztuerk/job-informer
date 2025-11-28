import type { DbSummary, Job, JobStats, JobsResponse, RunRequest, RunStatus, RunSummary } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const API_TOKEN = import.meta.env.VITE_API_TOKEN;

function authHeaders(extra?: Record<string, string>): HeadersInit {
  const headers: Record<string, string> = extra ? { ...extra } : {};
  if (API_TOKEN) {
    headers["x-api-token"] = API_TOKEN;
  }
  return headers;
}

function buildUrl(path: string, params?: Record<string, string | number | undefined>): string {
  const url = new URL(path, API_BASE.endsWith("/") ? API_BASE : `${API_BASE}/`);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
  }
  return url.toString();
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
  dateFrom?: string;
  dateTo?: string;
  sort?: "scraped_at_desc" | "scraped_at_asc" | "title_asc" | "title_desc";
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
      date_from: query.dateFrom,
      date_to: query.dateTo,
      sort: query.sort,
    })
  );
  return handleJson<JobsResponse>(res);
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), { headers: authHeaders() });
  return handleJson<Job>(res);
}

export async function deleteJob(jobId: string): Promise<void> {
  const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Delete failed with status ${res.status}`);
  }
}

export async function fetchStats(): Promise<JobStats> {
  const res = await fetch(buildUrl("stats"), { headers: authHeaders() });
  return handleJson<JobStats>(res);
}

export async function fetchDbSummary(): Promise<DbSummary> {
  const res = await fetch(buildUrl("db-summary"), { headers: authHeaders() });
  return handleJson<DbSummary>(res);
}
