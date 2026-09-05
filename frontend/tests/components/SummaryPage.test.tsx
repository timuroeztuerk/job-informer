import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SummaryPage from "../../src/components/SummaryPage";
import { fetchDbSummary, listRuns } from "../../src/api";
import type { CollectionValidation, DbSummary, RunSummary } from "../../src/types";

vi.mock("../../src/api", () => ({
  fetchDbSummary: vi.fn(), listRuns: vi.fn(),
  getApiErrorMessage: (_error: unknown, fallback: string) => `${fallback} Check VITE_API_BASE.`,
}));

const snapshot: DbSummary = {
  scope: { window: "7d", as_of: "2026-09-05T12:00:00Z", seen_since: "2026-08-29T12:00:00Z", recent_since: "2026-08-29T12:00:00Z", all_active_jobs: 20 },
  totals: { total_jobs: 12, archived_jobs: 4, recent_jobs_7_days: 3, companies: 5, locations: 3, repeated_jobs: 6,
    date_range: { earliest: "2026-07-01T00:00:00Z", latest: "2026-09-04T00:00:00Z" } },
  review: { unmatched_jobs: 4, automatic_archives: 3, favorite_jobs: 1 },
  jobs_by_source: { LinkedIn: 12 },
  top_companies: [{ key: "ACME", name: "ACME", count: 4, percentage: 33.3, new_jobs_7_days: 2, repeated_jobs: 3 }],
  top_locations: [{ key: "München", name: "München", count: 6, percentage: 50 }],
  role_families: [{ key: "analytics_bi", name: "analytics_bi", count: 7, percentage: 58.3 }, { key: "unclassified", name: "unclassified", count: 5, percentage: 41.7 }],
  query_groups: [{ key: "data_science", name: "Data science", count: 8, percentage: 66.7 }],
  recurring_jobs: [{ job_id: "linkedin:repeat", title: "Returning data role", company: "ACME", location: "München", source: "LinkedIn", is_favorite: false, seen_count: 4, last_seen_at: "2026-09-04T08:00:00Z" }],
  collection_freshness: { as_of: "2026-09-05T12:00:00Z", last_collected_at: "2026-09-05T08:00:00Z", latest_observation_at: "2026-09-05T08:00:00Z", latest_scrape_at: "2026-09-05T08:00:00Z", source: "job_observation", age_days: 0.16, status: "fresh" },
};
const run: RunSummary = {
  run_id: "run-1", mode: "run-once", status: "succeeded", started_at: "2026-09-05T08:00:00Z", time_range: "day",
  metrics: { observed: 12, new: 3, archived: 2 },
  query_coverage: [{ query_group_key: "data_science", query_text: "Data Scientist", location: "Germany", page_offsets: [0, 10], pages_attempted: 2, pages_completed: 2, raw_cards: 20, valid_jobs: 20, duplicate_cards: 0, request_failures: 0, rate_limit_responses: 0, stop_reason: "page_limit" }],
  scope_comparison: {
    countrywide: { locations: ["Germany"], queries: 1, unique_jobs: 4, pages_attempted: 2, pages_completed: 2, page_completion_percent: 100, request_failures: 0 },
    cities: { locations: ["Berlin"], queries: 1, unique_jobs: 6, pages_attempted: 2, pages_completed: 2, page_completion_percent: 100, request_failures: 0 },
    shared_jobs: 2, countrywide_only_jobs: 2, city_only_jobs: 4, city_jobs_covered_by_countrywide_percent: 33.3,
  },
};

const evidence: CollectionValidation = {
  required_days: 3, healthy_days: 2, timezone: "Europe/Berlin",
  baseline: { keywords: "Data Scientist,Data Analyst", locations: "Germany,Switzerland,Berlin,Stuttgart,Frankfurt,München", time_range: "day" },
  matching_runs: 3, other_runs: 1,
  cities: [{ location: "Berlin", sampled_days: 2, days_adding_jobs: 2, unique_jobs: 100, shared_jobs: 25, city_only_jobs: 75 }],
  runs: [
    { run_id: "retry", started_at: "2026-09-05T09:00:00Z", date: "2026-09-05", healthy: false, counted: false, concerns: ["Request failures"], pages_attempted: 24, pages_completed: 23, city_only_jobs: 20, city_coverage_percent: 25 },
    { run_id: "day2", started_at: "2026-09-05T08:00:00Z", date: "2026-09-05", healthy: true, counted: true, concerns: [], pages_attempted: 24, pages_completed: 24, city_only_jobs: 25, city_coverage_percent: 25 },
    { run_id: "day1", started_at: "2026-09-04T08:00:00Z", date: "2026-09-04", healthy: true, counted: true, concerns: [], pages_attempted: 24, pages_completed: 24, city_only_jobs: 50, city_coverage_percent: 25 },
  ],
};

describe("Intelligence market overview", () => {
  beforeEach(() => {
    vi.mocked(fetchDbSummary).mockReset().mockResolvedValue(snapshot);
    vi.mocked(listRuns).mockReset().mockResolvedValue([run]);
  });

  it("balances rankings, role mix, recurring jobs, audits, and the latest collection", async () => {
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading your market overview");
    expect(await screen.findByRole("heading", { name: "Who appears in your results" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Where the sample is concentrated" })).toBeInTheDocument();
    expect(screen.getByText("What’s in the mix")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Returning data role/ })).toHaveTextContent("4sightings");
    expect(screen.getByText("Coverage looks good")).toBeInTheDocument();
    expect(screen.getByText(/country searches covered 33.3%/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Review this sample" })).toHaveTextContent("Automatically archived");
  });

  it("keeps exact company, normalized city, role, and query drill-downs in the snapshot window", async () => {
    window.history.replaceState({}, "", "/?view=summary&archived=only&relevance_outcome=auto_archived&page=7&favorite=1");
    const user = userEvent.setup();
    const onOpenDashboard = vi.fn();
    render(<SummaryPage onOpenDashboard={onOpenDashboard} />);
    await user.click(await screen.findByRole("button", { name: "ACME, 4 jobs" }));
    let params = new URLSearchParams(window.location.search);
    expect(params.get("company")).toBe("ACME");
    expect(params.get("company_exact")).toBe("1");
    expect(params.get("seen_since")).toBe(snapshot.scope.seen_since);
    for (const key of ["archived", "relevance_outcome", "page", "favorite"]) expect(params.has(key)).toBe(false);
    await user.click(screen.getByRole("button", { name: "München, 6 jobs" }));
    params = new URLSearchParams(window.location.search);
    expect(params.get("location")).toBe("München");
    expect(params.get("location_primary")).toBe("1");
    expect(params.has("company_exact")).toBe(false);
    await user.click(screen.getByRole("button", { name: /Analytics & BI/ }));
    expect(new URLSearchParams(window.location.search).get("role")).toBe("analytics_bi");
    await user.click(screen.getByRole("button", { name: /Data science 8/ }));
    expect(new URLSearchParams(window.location.search).get("query_group")).toBe("data_science");
    expect(onOpenDashboard).toHaveBeenCalledTimes(4);
  });

  it("opens recurring jobs and archive audits with the correct filters", async () => {
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /Returning data role/ }));
    let params = new URLSearchParams(window.location.search);
    expect(params.get("job")).toBe("linkedin:repeat");
    expect(params.get("repeated")).toBe("1");
    expect(params.get("sort")).toBe("seen_count_desc");
    await user.click(screen.getByRole("button", { name: /Automatically archived/ }));
    params = new URLSearchParams(window.location.search);
    expect(params.get("archived")).toBe("only");
    expect(params.get("relevance_outcome")).toBe("auto_archived");
    expect(params.get("seen_since")).toBe(snapshot.scope.seen_since);
    expect(params.has("repeated")).toBe(false);
    await user.click(screen.getByRole("button", { name: /New in 7 days/ }));
    expect(new URLSearchParams(window.location.search).get("from")).toBe(snapshot.scope.recent_since);
  });

  it("loads saved windows and ignores older responses when the window changes", async () => {
    window.history.replaceState({}, "", "/?intel_window=30d");
    let resolveOld!: (value: DbSummary) => void;
    vi.mocked(fetchDbSummary).mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValue({ ...snapshot, scope: { ...snapshot.scope, window: "all", seen_since: null } });
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    expect(fetchDbSummary).toHaveBeenCalledWith("30d");
    await user.click(screen.getByRole("button", { name: "All active jobs" }));
    await screen.findByText("Who appears in your results");
    await act(async () => resolveOld({ ...snapshot, totals: { ...snapshot.totals, total_jobs: 999 } }));
    expect(screen.queryByText("999")).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("intel_window")).toBe("all");
    await user.click(screen.getByRole("button", { name: "ACME, 4 jobs" }));
    expect(new URLSearchParams(window.location.search).has("seen_since")).toBe(false);
  });

  it("offers the full archive when no jobs were seen recently", async () => {
    vi.mocked(fetchDbSummary).mockResolvedValue({ ...snapshot, totals: { ...snapshot.totals, total_jobs: 0 }, top_companies: [], top_locations: [], recurring_jobs: [] });
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    expect(await screen.findByText("No sightings in this window")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show all active jobs" }));
    await waitFor(() => expect(fetchDbSummary).toHaveBeenLastCalledWith("all"));
  });

  it("flags a succeeded run with empty or incomplete queries as partial coverage", async () => {
    vi.mocked(listRuns).mockResolvedValue([{ ...run, query_coverage: [{ ...run.query_coverage![0], valid_jobs: 0, pages_completed: 1, request_failures: 1, stop_reason: "request_failed" }] }]);
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    expect(await screen.findByText("Check query coverage")).toBeInTheDocument();
    expect(screen.queryByText("Coverage looks good")).not.toBeInTheDocument();
    expect(screen.queryByText(/City searches are still adding coverage/)).not.toBeInTheDocument();
    await user.click(screen.getByText("1 query needs a closer look"));
    expect(screen.getByText(/0 jobs, 1\/2 pages, 1 request failures/)).toBeInTheDocument();
  });

  it("keeps the market usable when collection diagnostics fail", async () => {
    vi.mocked(listRuns).mockRejectedValue(new Error("Unavailable"));
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "ACME, 4 jobs" })).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load the latest collection");
  });

  it("shows a useful retry state if the market request fails", async () => {
    vi.mocked(fetchDbSummary).mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load intelligence from the API");
    expect(screen.queryByRole("region", { name: "Market totals" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(fetchDbSummary).toHaveBeenCalledTimes(2);
  });

  it("shows comparable days, per-city evidence, and failures without claiming filter validation", async () => {
    vi.mocked(fetchDbSummary).mockResolvedValue({ ...snapshot, collection_validation: evidence });
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    const section = await screen.findByRole("region", { name: "Evidence across collection days" });
    expect(section).toHaveTextContent("2 of 3 days collected");
    expect(section).toHaveTextContent("1 more separate day");
    expect(within(section).getByRole("table", { name: "What each retained city adds" })).toHaveTextContent("Berlin2 / 27525.0%");
    expect(section).toHaveTextContent("Filter quality still needs manual review");
    await user.click(within(section).getByText("Inspect 3 matching collection attempts"));
    expect(within(section).getByText("Request failures")).toBeVisible();
    expect(within(section).getAllByText("Counted day")).toHaveLength(2);
  });

  it("opens validation audit lists across the archive and clears market drill-down filters", async () => {
    window.history.replaceState({}, "", "/?view=summary&company=ACME&favorite=1&repeated=1&seen_since=2026-09-01&page=4");
    vi.mocked(fetchDbSummary).mockResolvedValue({ ...snapshot, collection_validation: evidence });
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "Review flagged examples ↗" }));
    let params = new URLSearchParams(window.location.search);
    expect(params.get("flagged")).toBe("1");
    expect(params.get("archived")).toBe("include");
    for (const name of ["company", "favorite", "repeated", "seen_since", "page", "relevance_outcome"]) expect(params.has(name)).toBe(false);
    await user.click(screen.getByRole("button", { name: "Check automatic archives ↗" }));
    params = new URLSearchParams(window.location.search);
    expect(params.get("relevance_outcome")).toBe("auto_archived");
    expect(params.get("archived")).toBe("only");
    expect(params.has("flagged")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Review unmatched jobs ↗" }));
    params = new URLSearchParams(window.location.search);
    expect(params.get("relevance_outcome")).toBe("unmatched");
    expect(params.has("archived")).toBe(false);
  });

  it("keeps manual review as the next step when three healthy collection days exist", async () => {
    vi.mocked(fetchDbSummary).mockResolvedValue({ ...snapshot, collection_validation: { ...evidence, healthy_days: 3 } });
    render(<SummaryPage onOpenDashboard={vi.fn()} />);
    const section = await screen.findByRole("region", { name: "Evidence across collection days" });
    expect(section).toHaveTextContent("3 of 3 days collected");
    expect(section).toHaveTextContent("Review filter quality and city-only roles before moving on");
    expect(section).not.toHaveTextContent("more separate");
  });
});
