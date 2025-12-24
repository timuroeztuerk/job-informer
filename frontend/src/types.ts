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
  parsed_description?: ParsedDescription | null;
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
  status: "running" | "succeeded" | "failed";
  return_code?: number | null;
  log_tail?: string;
  started_at: string;
  finished_at?: string | null;
  keywords?: string | null;
  locations?: string | null;
  time_range?: string | null;
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
  status: "running" | "succeeded" | "failed";
  return_code?: number | null;
  started_at: string;
  finished_at?: string | null;
  keywords?: string | null;
  locations?: string | null;
  time_range?: string | null;
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
  parsed_descriptions_stats?: Record<string, number>;
  parsed_insights: ParsedInsights;
  city_summary: CitySummary;
}

export interface SenioritySlice {
  count: number;
  percentage?: number;
  avg_experience?: number | null;
  avg_salary?: number | null;
}
