export type JobArchiveFilter = "exclude" | "include" | "only";
export type JobRelevanceOutcome = "target" | "excluded" | "unrelated" | "unmatched" | "manual_keep" | "manual_archive";
export type JobRelevanceFilter = JobRelevanceOutcome | "auto_archived";

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
  is_favorite: boolean;
  is_flagged?: boolean;
  flag_reason?: string | null;
  flagged_at?: string | null;
  archived_at?: string | null;
  archived_reason?: string | null;
  first_seen_at?: string;
  last_seen_at?: string;
  seen_count?: number;
  relevance_outcome?: JobRelevanceOutcome | null;
  role_family?: string | null;
  relevance_reason?: string | null;
  relevance_ruleset_version?: string | null;
  relevance_evaluated_at?: string | null;
  query_groups?: string[];
  query_matches?: JobQueryMatch[];
  posting_count?: number;
  postings?: { job_id: string; location: string; url?: string; first_seen_at?: string; last_seen_at?: string }[];
}

export interface JobFlag {
  job_id: string;
  is_flagged: boolean;
  flag_reason: string | null;
  flagged_at: string | null;
}

export interface AIQueueState {
  configured: boolean;
  paused: boolean;
  reason: string | null;
  cooldown_until: number;
  pilot_limit: number | null;
  estimated_cost_usd: number;
  counts: Record<string, number>;
  jobs: Array<{ job_id: string; title: string; company: string; status: string | null }>;
  usage: { input_tokens: number; output_tokens: number; cached_tokens: number; reasoning_tokens: number };
  model: string;
  reasoning: string;
  service_tier: string;
  added?: number;
}

export interface AIEvidence { source_ref: string; quote: string; start: number; end: number }
export interface AIClaim {
  evidence: AIEvidence[];
  term?: string;
  canonical_term?: string;
  category?: string;
  value?: string;
  language?: string;
  proficiency?: string | null;
  cefr?: string | null;
  wording?: string;
  minimum_years?: number | null;
  maximum_years?: number | null;
  scope?: string | null;
  mode?: string;
  office_attendance?: string | null;
  geographic_restrictions?: string | null;
  strength?: string;
  alternative_group?: string | null;
  field?: string;
  explanation?: string;
  code?: string;
}

export interface AIJobResult {
  rejected?: unknown;
  job_id: string;
  status: string | null;
  error: string | null;
  stale: boolean;
  queue: { paused: boolean; cooldown_until: number };
  source: { source_id: number; job_id: string; source_url: string; fetched_at: string } | null;
  input: { sources: Record<string, string>; source_quality: string; criteria: Array<{ label: string; value: string }> } | null;
  saved: {
    extracted_at: string;
    fields: {
      description_language: string;
      description_languages: AIClaim[];
      states: Record<string, string>;
      requirements: AIClaim[];
      languages: AIClaim[];
      experience: AIClaim[];
      work_arrangement: AIClaim[];
      responsibilities: AIClaim[];
      seniority: AIClaim[];
      employment_type: AIClaim[];
      conflicts: AIClaim[];
    };
    contract: { model: string; reasoning: string; schema_version: string };
    metadata: { model: string; service_tier: string; latency_ms: number; usage: Record<string, number> };
    validation: { schema: boolean; evidence: boolean; human_reviewed: boolean };
  } | null;
  legacy: { model: string; version: number; created_at: string; fields: Record<string, unknown> } | null;
  legacy_review: { status: string; priority: string } | null;
}

export interface DescriptionQueueState {
  paused: boolean;
  reason: string | null;
  cooldown_until: string | null;
  queued: number;
  fetching: number;
  saved_jobs: number;
  failed_jobs: number;
  added?: number;
}

export interface JobDescriptionResponse {
  job_id: string;
  saved: {
    source_kind?: "public_page" | "legacy_text";
    source_id: number;
    source_url: string;
    fetched_at: string;
    extraction_id: number;
    extractor: string;
    extractor_version: string;
    schema_version: string;
    extracted_at: string;
    content_sha256: string;
    data: {
      description_text: string;
      title: string | null;
      criteria: { key: string; label: string; value: string; evidence: { selector: string; text: string } }[];
      evidence: { source_url: string; description_locator: string };
    };
  } | null;
  attempts: {
    fetch_id: number;
    requested_at: string;
    started_at: string | null;
    finished_at: string | null;
    status: "queued" | "fetching" | "succeeded" | "unchanged" | "failed" | "interrupted";
    http_status: number | null;
    error_kind: string | null;
    error_message: string | null;
    source_id: number | null;
  }[];
  source_versions: number;
  reparse_available: boolean;
  parser_version: string;
  queue: DescriptionQueueState;
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

export interface CollectionScopeMetrics {
  locations: string[];
  queries: number;
  unique_jobs: number;
  pages_attempted: number;
  pages_completed: number;
  page_completion_percent: number;
  request_failures: number;
}

export interface CollectionScopeComparison {
  countrywide: CollectionScopeMetrics;
  cities: CollectionScopeMetrics;
  shared_jobs: number;
  countrywide_only_jobs: number;
  city_only_jobs: number;
  city_jobs_covered_by_countrywide_percent: number;
  per_city?: { location: string; unique_jobs: number; shared_jobs: number; city_only_jobs: number }[];
}

export interface CollectionValidation {
  required_days: number;
  healthy_days: number;
  timezone: string;
  baseline: { keywords: string; locations: string; time_range: string } | null;
  matching_runs: number;
  other_runs: number;
  runs: {
    run_id: string;
    started_at: string;
    date: string;
    healthy: boolean;
    counted: boolean;
    concerns: string[];
    pages_completed: number;
    pages_attempted: number;
    city_only_jobs: number | null;
    city_coverage_percent: number | null;
  }[];
  cities: {
    location: string;
    sampled_days: number;
    days_adding_jobs: number;
    unique_jobs: number;
    shared_jobs: number;
    city_only_jobs: number;
  }[];
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
  scope_comparison?: CollectionScopeComparison | null;
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
  key: string;
  name: string;
  count: number;
  percentage?: number;
  new_jobs_7_days?: number;
  repeated_jobs?: number;
}

export type IntelligenceWindow = "all" | "7d" | "30d";

export interface DbSummary {
  scope: {
    window: IntelligenceWindow;
    as_of: string;
    seen_since: string | null;
    recent_since: string;
    all_active_jobs: number;
  };
  totals: {
    total_jobs: number;
    archived_jobs: number;
    recent_jobs_7_days: number;
    companies: number;
    locations: number;
    repeated_jobs: number;
    date_range: { earliest: string | null; latest: string | null };
  };
  review: { unmatched_jobs: number; automatic_archives: number; favorite_jobs: number };
  jobs_by_source: Record<string, number>;
  top_companies: CountStat[];
  top_locations: CountStat[];
  role_families?: CountStat[];
  query_groups?: CountStat[];
  collection_freshness: CollectionFreshness;
  recurring_jobs: Job[];
  collection_validation?: CollectionValidation;
}
