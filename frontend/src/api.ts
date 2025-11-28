import type { Job, JobStats, JobsResponse, RunRequest, RunStatus } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return handleJson<RunStatus>(res);
}

export async function getRunStatus(runId: string): Promise<RunStatus> {
  const res = await fetch(buildUrl(`runs/${runId}`));
  return handleJson<RunStatus>(res);
}

interface JobsQuery {
  limit?: number;
  offset?: number;
  search?: string;
  location?: string;
  source?: string;
}

export async function fetchJobs(query: JobsQuery = {}): Promise<JobsResponse> {
  const res = await fetch(
    buildUrl("jobs", {
      limit: query.limit ?? 25,
      offset: query.offset ?? 0,
      search: query.search,
      location: query.location,
      source: query.source,
    })
  );
  return handleJson<JobsResponse>(res);
}

export async function fetchJob(jobId: string): Promise<Job> {
  const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`));
  return handleJson<Job>(res);
}

export async function fetchStats(): Promise<JobStats> {
  const res = await fetch(buildUrl("stats"));
  return handleJson<JobStats>(res);
}
