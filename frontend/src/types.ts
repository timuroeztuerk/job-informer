export type JobArchiveFilter = "exclude" | "include" | "only";

export interface QueryGroupOption {
  key: string;
  name: string;
}

export interface JobQueryMatch {
  query_group_key: string;
  query_group_name: string;
  query_text: string;
  location: string;
  first_page_offset?: number | null;
  last_matched_at?: string | null;
  match_runs?: number;
}

export interface Job {
  job_id: string;
  title: string;
  company: string;
  location: string;
  source: string;
  url?: string;
  salary?: string;
  scraped_at?: string;
  created_at?: string;
  archived_at?: string | null;
  archived_reason?: string | null;
  first_seen_at?: string;
  last_seen_at?: string;
  seen_count?: number;
  relevance_outcome?: "target" | "excluded" | "unrelated" | "unmatched" | "manual_keep" | "manual_archive" | null;
  role_family?: string | null;
  relevance_reason?: string | null;
  relevance_ruleset_version?: string | null;
  relevance_evaluated_at?: string | null;
  query_groups?: string[];
  query_matches?: JobQueryMatch[];
}

export interface JobsResponse {
  total: number;
  count: number;
  items: Job[];
}

export type CliMode = "run-once";

export interface RunMetrics {
  observed: number;
  new: number;
  archived: number;
  queries?: number;
  pages_attempted?: number;
  pages_completed?: number;
  request_failures?: number;
  rate_limit_responses?: number;
  relevance_target?: number;
  relevance_excluded?: number;
  relevance_unrelated?: number;
  relevance_unmatched?: number;
  relevance_manual_keep?: number;
  relevance_manual_archive?: number;
  target_yield_percent?: number;
  query_contamination_percent?: number;
  relevance_conflicts?: number;
}

export interface QueryCoverage {
  query_group_key: string;
  display_name?: string | null;
  query_text: string;
  location: string;
  page_offsets: number[];
  pages_attempted: number;
  pages_completed: number;
  raw_cards: number;
  valid_jobs: number;
  duplicate_cards: number;
  request_failures: number;
  rate_limit_responses: number;
  stop_reason?: string | null;
  last_status?: number | null;
  last_error?: string | null;
}

export interface RunProgressEvent {
  at: string;
  level: "info" | "warning" | "error";
  message: string;
}

export interface RunProgress {
  stage: string;
  label: string;
  current_query?: string | null;
  completed_queries?: number | null;
  total_queries?: number | null;
  metrics?: RunMetrics | null;
  updated_at: string;
  events: RunProgressEvent[];
}

export interface RunStatus {
  run_id: string;
  mode: CliMode;
  status: "starting" | "running" | "succeeded" | "failed" | "interrupted";
  return_code?: number | null;
  log_tail?: string;
  started_at: string;
  finished_at?: string | null;
  keywords?: string | null;
  locations?: string | null;
  time_range?: string | null;
  trigger?: "manual";
  pid?: number | null;
  metrics?: RunMetrics | null;
  query_coverage?: QueryCoverage[] | null;
  progress?: RunProgress | null;
}

export type RunSummary = RunStatus;

export interface RunRequest {
  mode?: "run-once";
  keywords?: string;
  locations?: string;
  time_range?: string;
}

export interface CollectionFreshness {
  as_of: string;
  last_collected_at: string | null;
  latest_observation_at: string | null;
  latest_scrape_at: string | null;
  source: "job_observation" | "job_record" | null;
  age_days: number | null;
  status: "fresh" | "aging" | "stale" | "empty";
  stale_after_days?: number;
}

export interface JobStats {
  total_jobs: number;
  archived_jobs?: number;
  jobs_by_source: Record<string, number>;
  top_companies: Record<string, number>;
  recent_jobs_7_days: number;
  date_range: { earliest: string | null; latest: string | null };
  sources_list?: string[];
  companies_list?: string[];
  role_families_list?: string[];
  query_groups_list?: QueryGroupOption[];
  collection_freshness?: CollectionFreshness;
  database?: {
    status: "ready" | "not_ready";
    instance_name: string;
    db_path: string;
    schema_ok: boolean;
    missing_tables: string[];
    total_jobs: number;
    active_jobs: number;
    db_size_bytes: number;
    db_modified_at: string | null;
    error?: string;
  };
}

export interface CountStat {
  name: string;
  count: number;
  percentage?: number;
}

export interface DbSummary {
  totals: {
    total_jobs: number;
    archived_jobs: number;
    recent_jobs_7_days: number;
    date_range: { earliest: string | null; latest: string | null };
  };
  jobs_by_source: Record<string, number>;
  top_companies: CountStat[];
  top_locations: CountStat[];
  role_families?: CountStat[];
  query_groups?: CountStat[];
  collection_freshness: CollectionFreshness;
}
