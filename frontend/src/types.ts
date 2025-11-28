export interface Job {
  job_id: string;
  title: string;
  company: string;
  location: string;
  source: string;
  url?: string;
  salary?: string;
  description?: string;
  scraped_at?: string;
  created_at?: string;
}

export interface JobsResponse {
  total: number;
  count: number;
  items: Job[];
}

export interface RunStatus {
  run_id: string;
  status: "running" | "succeeded" | "failed";
  return_code?: number | null;
  log_tail?: string;
  started_at: string;
  finished_at?: string | null;
}

export interface RunRequest {
  keywords?: string;
  locations?: string;
  time_range?: string;
}

export interface JobStats {
  total_jobs: number;
  jobs_by_source: Record<string, number>;
  top_companies: Record<string, number>;
  recent_jobs_7_days: number;
  date_range: {
    earliest: string | null;
    latest: string | null;
  };
}
