export type JobAnnotationStatus =
  | "unreviewed"
  | "interesting"
  | "applied"
  | "interviewing"
  | "offer"
  | "rejected"
  | "archived";

export type JobAnnotationPriority = "low" | "medium" | "high";

export type JobArchiveFilter = "exclude" | "include" | "only";

export interface JobAnnotation {
  status: JobAnnotationStatus;
  priority: JobAnnotationPriority;
  notes: string;
  why_interesting: string;
  skill_gaps: string[];
  follow_up_date?: string | null;
  resume_version: string;
  created_at?: string | null;
  updated_at?: string | null;
}

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
  archived_at?: string | null;
  archived_reason?: string | null;
  first_seen_at?: string;
  last_seen_at?: string;
  seen_count?: number;
  parsed_description?: ParsedDescription | null;
  annotation?: JobAnnotation | null;
}

export interface JobsResponse {
  total: number;
  count: number;
  items: Job[];
}

export type CliMode =
  | "run-once"
  | "purge"
  | "reset-ai-purge"
  | "parse-descriptions"
  | "db-summary"
  | "test"
  | "refetch-titles";

export interface ParsedPayload {
  seniority?: string;
  employment_type?: string;
  remote?: string;
  languages?: string[];
  programming_languages?: string[];
  tools?: string[];
  skills?: string[];
  degree_field?: string;
  degree_type?: string;
  years_experience_min?: number | null;
  location?: string[];
  salary_eur_range?: { min?: number | null; max?: number | null };
  "extra benefits"?: string | string[];
  summary?: string;
}

export interface ParsedDescription {
  payload?: ParsedPayload | null;
  version?: number;
  created_at?: string;
  raw?: string;
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
  trigger?: "manual" | "scheduled";
  pid?: number | null;
  metrics?: RunMetrics | null;
}

export interface RunMetrics {
  observed: number;
  new: number;
  archived: number;
  descriptions_fetched: number;
  parsed: number;
}

export interface RunRequest {
  mode?: CliMode;
  keywords?: string;
  locations?: string;
  time_range?: string;
}

export interface RunSummary {
  run_id: string;
  mode: CliMode;
  status: "starting" | "running" | "succeeded" | "failed" | "interrupted";
  return_code?: number | null;
  started_at: string;
  finished_at?: string | null;
  keywords?: string | null;
  locations?: string | null;
  time_range?: string | null;
  trigger?: "manual" | "scheduled";
  pid?: number | null;
  metrics?: RunMetrics | null;
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
  sources_list?: string[];
  companies_list?: string[];
  collection_freshness?: CollectionFreshness;
  collection_scheduler?: {
    enabled: boolean;
    interval_hours: number;
    last_successful_run_at: string | null;
  };
}

export interface CountStat {
  name: string;
  count: number;
  percentage?: number;
}

export interface NumericRangeStat {
  count: number;
  average: number | null;
  min: number | null;
  max: number | null;
}

export interface ParsedInsights {
  coverage_pct?: number | null;
  orphaned_jobs?: number;
  historical_payloads?: number | null;
  seniority_mix?: {
    senior: SenioritySlice;
    non_senior: SenioritySlice;
  };
  total_records: number;
  programming_languages: CountStat[];
  skills: CountStat[];
  tools: CountStat[];
  seniority_levels: CountStat[];
  employment_types: CountStat[];
  remote_options: CountStat[];
  languages: CountStat[];
  degree_fields: CountStat[];
  degree_types: CountStat[];
  experience_years: NumericRangeStat;
  salary_eur: NumericRangeStat;
}

export interface CitySummary {
  total_jobs: number;
  top_cities: CountStat[];
}

export interface TrendDelta {
  name: string;
  recent_count: number;
  previous_count: number;
  delta: number;
}

export interface WeeklyJobCount {
  week_start: string;
  jobs: number;
  parsed_jobs: number;
}

export interface TrendWindow {
  start: string | null;
  end: string | null;
  jobs: number;
}

export interface TrendSummary {
  window_days: number;
  recent_window: TrendWindow;
  previous_window: TrendWindow;
  weekly_job_counts: WeeklyJobCount[];
  momentum: {
    skills: TrendDelta[];
    tools: TrendDelta[];
    programming_languages: TrendDelta[];
    companies: TrendDelta[];
    cities: TrendDelta[];
  };
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

export interface FitSummaryJob {
  job_id: string;
  title: string;
  company: string;
  score: number;
  band: string;
  reasons: string[];
}

export interface ProfileFitSummary {
  profile_id: string;
  profile_name: string;
  profile_version: number;
  total_scored: number;
  average_score: number | null;
  band_counts: Record<string, number>;
  top_jobs: FitSummaryJob[];
}

export interface ParserTelemetrySummary {
  attempts: number;
  success_count: number;
  failure_count: number;
  refusal_count: number;
  recent_window_days: number;
  recent_attempts: number;
  recent_success_rate: number | null;
  average_latency_ms: number | null;
  exhausted_retries: number;
  jobs_with_current_failed_status: number;
}

export interface SkillGapSummary {
  jobs_with_gaps: number;
  top_gaps: CountStat[];
}

export interface ObservationStats {
  total_observations: number;
  repeat_jobs: number;
  average_seen_count: number | null;
  max_seen_count: number;
  total_scrape_runs?: number;
}

export interface RecurringJobSummary {
  job_id: string;
  title: string;
  company: string;
  seen_count: number;
  active_days: number;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
}

export interface ObservationSummary {
  total_observations: number;
  repeat_jobs: number;
  average_seen_count: number | null;
  max_seen_count: number;
  top_recurring_jobs: RecurringJobSummary[];
}

export interface DbSummary {
  totals: {
    total_jobs: number;
    recent_jobs_7_days: number;
    date_range: {
      earliest: string | null;
      latest: string | null;
    };
  };
  jobs_by_source: Record<string, number>;
  top_companies: Record<string, number>;
  observation_stats?: ObservationStats;
  observation_summary?: ObservationSummary;
  collection_freshness?: CollectionFreshness;
  profile_fit_summary?: ProfileFitSummary;
  parser_telemetry?: ParserTelemetrySummary;
  integrity?: {
    sqlite_ok: boolean;
    sqlite_messages: string[];
    orphan_record_count: number;
    orphan_record_counts: Record<string, number>;
  };
  skill_gap_summary?: SkillGapSummary;
  parsed_descriptions_stats?: Record<string, number>;
  parsed_insights: ParsedInsights;
  city_summary: CitySummary;
  trend_summary: TrendSummary;
}

export interface SenioritySlice {
  count: number;
  percentage?: number;
  avg_experience?: number | null;
  avg_salary?: number | null;
}
