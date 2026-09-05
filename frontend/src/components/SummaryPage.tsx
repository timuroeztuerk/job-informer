import React, { useCallback, useEffect, useRef, useState } from "react";
import { fetchDbSummary, getApiErrorMessage, listRuns } from "../api";
import type { CountStat, DbSummary, IntelligenceWindow, RunSummary } from "../types";
import { readEnumParam, replaceSearchParams } from "../urlState";
import CollectionEvidence from "./CollectionEvidence";
import { jobLocation } from "../jobPresentation";

interface SummaryPageProps { className?: string; onOpenDashboard: () => void }
const windows: { value: IntelligenceWindow; label: string }[] = [
  { value: "7d", label: "Seen in 7 days" }, { value: "30d", label: "Seen in 30 days" }, { value: "all", label: "All active jobs" },
];
const number = (value: number) => value.toLocaleString();
const percent = (value: number, total: number) => total ? `${(value / total * 100).toFixed(1)}%` : "0%";
const formatDate = (value?: string | null, withTime = false) => {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return new Intl.DateTimeFormat("en", withTime
    ? { dateStyle: "medium", timeStyle: "short" }
    : { month: "short", day: "numeric", year: "numeric" }).format(date);
};
const roleName = (key: string) => ({ data_science: "Data science", analytics_bi: "Analytics & BI", unclassified: "Other / unclassified" }[key] || key.replace(/_/g, " "));
const roleColor = (key: string) => ({ data_science: "#2563eb", analytics_bi: "#0d9488", unclassified: "#cbd5e1" }[key] || "#8b5cf6");
const Arrow = () => <span aria-hidden="true">↗</span>;

function RoleMix({ items, total, onSelect }: { items: CountStat[]; total: number; onSelect: (key: string) => void }) {
  let start = 0;
  const segments = items.map((item) => {
    const end = start + (total ? item.count / total * 100 : 0);
    const segment = `${roleColor(item.key)} ${start}% ${end}%`;
    start = end;
    return segment;
  });
  const recognized = total - (items.find((item) => item.key === "unclassified")?.count || 0);
  return <div className="market-role-mix">
    <div className="market-donut" aria-hidden="true" style={{ background: total && segments.length ? `conic-gradient(${segments.join(",")})` : "#e2e8f0" }}>
      <div><strong>{number(recognized)}</strong><span>role matches</span></div>
    </div>
    <div className="market-role-legend">{items.map((item) => <button key={item.key} type="button" onClick={() => onSelect(item.key)}>
      <span className="market-swatch" style={{ background: roleColor(item.key) }} /><span>{roleName(item.key)}</span>
      <strong>{number(item.count)}</strong><small>{percent(item.count, total)}</small>
    </button>)}</div>
  </div>;
}

function CollectionSnapshot({ run, error, onOpenJobs }: { run: RunSummary | null; error: string | null; onOpenJobs: () => void }) {
  const queries = run?.query_coverage || [];
  const running = run?.status === "running" || run?.status === "starting";
  const concerns = queries.filter((q) => q.request_failures > 0 || q.valid_jobs === 0 || q.pages_attempted === 0 || q.pages_completed !== q.pages_attempted || !["page_limit", "short_page", "empty_page"].includes(q.stop_reason || ""));
  const expectedQueries = run?.progress?.total_queries;
  const missingQueries = expectedQueries != null && queries.length < expectedQueries;
  const healthy = run?.status === "succeeded" && queries.length > 0 && concerns.length === 0 && !missingQueries;
  const status = running ? "In progress" : healthy ? "Coverage looks good" : run?.status === "failed" || run?.status === "interrupted" ? "Run needs attention" : queries.length ? "Check query coverage" : "Coverage unavailable";
  const comparison = run?.scope_comparison;
  const pieces = comparison ? [
    { name: "Country only", count: comparison.countrywide_only_jobs, color: "#0d9488" },
    { name: "Both", count: comparison.shared_jobs, color: "#93c5fd" },
    { name: "City only", count: comparison.city_only_jobs, color: "#2563eb" },
  ] : [];
  const union = pieces.reduce((sum, item) => sum + item.count, 0);
  return <section className="market-panel market-collection" aria-labelledby="collection-title">
    <div className="market-section-heading">
      <div><p className="label">Collection quality</p><h3 id="collection-title">How complete is the latest sample?</h3></div>
      {run && !error && <span className={`market-status ${healthy ? "good" : running ? "neutral" : "caution"}`}>{status}</span>}
    </div>
    {error ? <p className="error" role="alert">{error}</p> : !run ? <p className="muted">No collection run yet. Start a manual collection from Jobs.</p> : <>
      <div className="market-collection-body">
        <div className="market-run-facts">
          <p className="muted small">{formatDate(run.started_at, true)} · {run.time_range ? `${run.time_range} search window` : "configured search window"}</p>
          <div className="market-run-numbers">
            <div><strong>{run.metrics ? number(run.metrics.observed) : "—"}</strong><span>jobs observed</span></div>
            <div><strong>{run.metrics ? number(run.metrics.new) : "—"}</strong><span>new to archive</span></div>
            <div><strong>{queries.length ? `${queries.reduce((n, q) => n + q.pages_completed, 0)}/${queries.reduce((n, q) => n + q.pages_attempted, 0)}` : "—"}</strong><span>pages returned</span></div>
          </div>
          <p className="muted small">{healthy ? "All recorded queries returned jobs without request failures. Results remain limited to the configured pages." : running ? "Results are still arriving. Refresh after the run finishes." : "Treat these results as partial until missing pages, empty queries, or request failures are checked."}</p>
        </div>
        <div className="market-overlap">{comparison ? <>
          <div className="market-overlap-heading"><strong>Country vs city searches</strong><span>{number(union)} unique jobs</span></div>
          <div className="market-overlap-bar" aria-hidden="true">{pieces.map((piece) => <span key={piece.name} style={{ width: `${union ? piece.count / union * 100 : 0}%`, background: piece.color }} />)}</div>
          <div className="market-overlap-legend">{pieces.map((piece) => <span key={piece.name}><i className="market-swatch" style={{ background: piece.color }} />{piece.name} <strong>{number(piece.count)}</strong></span>)}</div>
          <p className="muted small">{healthy ? "In this run, " : "In the available results, "}country searches covered {comparison.city_jobs_covered_by_countrywide_percent}% of city results.{healthy && comparison.city_only_jobs > 0 ? " City searches are still adding coverage." : ""}</p>
        </> : <p className="muted small">A country/city comparison will appear after a run includes both search scopes.</p>}</div>
      </div>
      {concerns.length > 0 && !running && <details className="market-query-issues"><summary>{concerns.length} {concerns.length === 1 ? "query needs" : "queries need"} a closer look</summary>
        <ul>{concerns.map((q) => <li key={`${q.query_text}-${q.location}`}><strong>{q.query_text} · {q.location}</strong> — {q.valid_jobs} jobs, {q.pages_completed}/{q.pages_attempted} pages, {q.request_failures} request failures · {(q.stop_reason || "unfinished").replace(/_/g, " ")}</li>)}</ul>
      </details>}
    </>}
    <button type="button" className="market-text-link" onClick={onOpenJobs}>Open Jobs <Arrow /></button>
  </section>;
}

const SummaryPage: React.FC<SummaryPageProps> = ({ className, onOpenDashboard }) => {
  const [window, setWindow] = useState<IntelligenceWindow>(() => readEnumParam("intel_window", windows.map((item) => item.value)) || "7d");
  const [summary, setSummary] = useState<DbSummary | null>(null);
  const [latestRun, setLatestRun] = useState<RunSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const requestId = useRef(0);
  const reload = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    const [market, runs] = await Promise.allSettled([fetchDbSummary(window), listRuns(1)]);
    if (id !== requestId.current) return;
    if (market.status === "fulfilled") { setSummary(market.value); setError(null); }
    else setError(getApiErrorMessage(market.reason, "Could not load intelligence from the API."));
    if (runs.status === "fulfilled") { setLatestRun(runs.value[0] || null); setRunError(null); }
    else setRunError(getApiErrorMessage(runs.reason, "Could not load the latest collection."));
    setLoading(false);
  }, [window]);
  useEffect(() => {
    replaceSearchParams({ intel_window: window });
    setSummary(null);
    void reload();
    return () => { requestId.current += 1; };
  }, [reload, window]);
  const openJobs = useCallback((params: Record<string, string>, scoped = true) => {
    replaceSearchParams({
      view: "dashboard", search: "", location: "", source: "", company: "", company_exact: "", location_primary: "",
      role: "", query_group: "", relevance_outcome: "", favorite: "", flagged: "", from: "", to: "", sort: "", archived: "", job: "", page: "", repeated: "", status: "", priority: "",
      seen_since: scoped ? summary?.scope.seen_since || "" : "", ...params,
    });
    onOpenDashboard();
  }, [onOpenDashboard, summary]);
  const total = summary?.totals.total_jobs || 0;
  const freshness = summary?.collection_freshness;
  const topLocation = summary?.top_locations[0];
  return <div className={["summary-page market-page", className].filter(Boolean).join(" ")}>
    <section className="market-heading">
      <div><p className="label">Intelligence</p><h2>Your market, in focus</h2><p className="muted">Explore where jobs appear, what keeps returning, and how well your searches cover them.</p></div>
      <div className="market-heading-actions">
        {freshness && <span className={`market-status ${freshness.status === "fresh" ? "good" : "caution"}`}><span aria-hidden="true">●</span> {freshness.status === "fresh" ? "Recently collected" : freshness.status === "empty" ? "No sightings yet" : "Collection getting old"}</span>}
        <button type="button" className="ghost" onClick={() => void reload()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
      </div>
    </section>
    <div className="market-toolbar">
      <div className="market-window" role="group" aria-label="Market time window">{windows.map((item) => <button type="button" key={item.value} aria-pressed={window === item.value} onClick={() => setWindow(item.value)}>{item.label}</button>)}</div>
      <span className="muted small">Active jobs · {summary ? `Snapshot ${formatDate(summary.scope.as_of, true)}` : "Loading snapshot"}</span>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {loading && !summary && <div className="market-loading" role="status">Loading your market overview…</div>}
    {summary && <>
      <section className="market-metrics" aria-label="Market totals">
        <button type="button" className="market-metric" onClick={() => openJobs({})}><span>Active jobs <Arrow /></span><strong>{number(total)}</strong><small>{window === "all" ? "In your collected archive" : `Of ${number(summary.scope.all_active_jobs)} active in the archive`}</small></button>
        <button type="button" className="market-metric" onClick={() => openJobs({ from: summary.scope.recent_since })}><span>New in 7 days <Arrow /></span><strong>{number(summary.totals.recent_jobs_7_days)}</strong><small>First collected, not a repeat sighting</small></button>
        <div className="market-metric"><span>Companies represented</span><strong>{number(summary.totals.companies)}</strong><small>Across {number(summary.totals.locations)} locations</small></div>
        <button type="button" className="market-metric" onClick={() => openJobs({ repeated: "1", sort: "seen_count_desc" })}><span>Seen more than once <Arrow /></span><strong>{number(summary.totals.repeated_jobs)}</strong><small>{percent(summary.totals.repeated_jobs, total)} of this active sample</small></button>
      </section>
      {total === 0 ? <section className="market-panel market-empty">
        <h3>{summary.scope.all_active_jobs ? "No sightings in this window" : "Your market overview starts with a collection"}</h3>
        <p className="muted">{summary.scope.all_active_jobs ? "Your active archive is still available. Widen the window to explore it." : "Collect jobs from the Jobs page to see companies, locations, and recurring roles here."}</p>
        <button type="button" className="primary" onClick={() => summary.scope.all_active_jobs ? setWindow("all") : openJobs({}, false)}>{summary.scope.all_active_jobs ? "Show all active jobs" : "Go to Jobs"}</button>
      </section> : <>
        <div className="market-grid">
          <section className="market-panel" aria-labelledby="companies-title">
            <div className="market-section-heading"><div><p className="label">Companies</p><h3 id="companies-title">Who appears in your results</h3></div><span className="market-count">Top {summary.top_companies.length}</span></div>
            <div className="market-company-head" aria-hidden="true"><span>Company</span><span>Jobs</span><span>New 7d</span><span>Seen again</span></div>
            <div className="market-company-list">{summary.top_companies.map((item, index) => <button type="button" className="market-company-row" key={item.key} aria-label={`${item.name}, ${item.count} jobs`} onClick={() => openJobs({ company: item.key, company_exact: "1" })}>
              <span className="market-rank">{String(index + 1).padStart(2, "0")}</span><span className="market-company-name" title={item.name}>{item.name}</span><strong>{number(item.count)}</strong><span className="market-new">{number(item.new_jobs_7_days || 0)}</span><span>{number(item.repeated_jobs || 0)}</span>
            </button>)}</div>
            <p className="market-footnote">Company names as listed, including recruiters. Select a row to inspect its jobs.</p>
          </section>
          <section className="market-panel" aria-labelledby="locations-title">
            <div className="market-section-heading"><div><p className="label">Locations</p><h3 id="locations-title">Where the sample is concentrated</h3></div><span className="market-count">Top {summary.top_locations.length}</span></div>
            <div className="market-locations">{summary.top_locations.map((item) => <button type="button" className="market-location-row" key={item.key} aria-label={`${item.name}, ${item.count} jobs`} onClick={() => openJobs({ location: item.key, location_primary: "1" })}>
              <span>{item.name}</span><strong>{number(item.count)} <small>{percent(item.count, total)}</small></strong><span className="market-location-bar" aria-hidden="true"><i style={{ width: `${item.count / (topLocation?.count || 1) * 100}%` }} /></span>
            </button>)}</div>
            <p className="market-footnote">Known city spellings are grouped, including München/Munich and Zürich/Zurich.</p>
          </section>
        </div>
        <div className="market-grid market-role-grid">
          <section className="market-panel" aria-labelledby="roles-title">
            <div className="market-section-heading"><div><p className="label">Roles & discovery</p><h3 id="roles-title">What’s in the mix</h3></div></div>
            <RoleMix items={summary.role_families || []} total={total} onSelect={(role) => openJobs({ role })} />
            <p className="market-footnote">Title-based role matches. Adjacent and unclassified roles remain available for review.</p>
            <div className="market-discovery"><p className="label">Found through</p><div>{(summary.query_groups || []).map((item) => <button type="button" key={item.key} onClick={() => openJobs({ query_group: item.key })}>{item.name} <strong>{number(item.count)}</strong> <Arrow /></button>)}</div><p className="market-footnote">Search groups overlap; the same job can appear in both.</p></div>
          </section>
          <section className="market-panel" aria-labelledby="recurring-title">
            <div className="market-section-heading"><div><p className="label">Recurring jobs</p><h3 id="recurring-title">Back in your searches</h3></div><button type="button" className="market-text-link" onClick={() => openJobs({ repeated: "1", sort: "seen_count_desc" })}>View all <Arrow /></button></div>
            {summary.recurring_jobs.length ? <div className="market-recurring-list">{summary.recurring_jobs.map((job) => <button type="button" key={job.job_id} className="market-recurring-row" onClick={() => openJobs({ job: job.job_id, repeated: "1", sort: "seen_count_desc" })}>
              <span className="market-repeat-badge"><strong>{number(job.seen_count || 1)}</strong><small>sightings</small></span><span className="market-recurring-copy"><strong>{job.title}</strong><span>{job.company} · {jobLocation(job)}</span><small>Last seen {formatDate(job.last_seen_at)}</small></span><Arrow />
            </button>)}</div> : <p className="market-no-repeats muted">No repeat sightings in this sample yet. Subsequent collections will surface returning postings here.</p>}
            <p className="market-footnote">Matching postings count as one job. Each collection run counts once, including multiple runs on one day.</p>
          </section>
        </div>
      </>}
      <section className="market-review" aria-label="Review this sample">
        <div><p className="label">Keep the sample useful</p><h3>A closer look at the filters</h3><p className="muted small">These counts follow the selected time window.</p></div>
        <button type="button" onClick={() => openJobs({ relevance_outcome: "unmatched" })}><strong>{number(summary.review.unmatched_jobs)}</strong><span>Unmatched to review <Arrow /></span></button>
        <button type="button" onClick={() => openJobs({ relevance_outcome: "auto_archived", archived: "only" })}><strong>{number(summary.review.automatic_archives)}</strong><span>Automatically archived <Arrow /></span></button>
        <button type="button" onClick={() => openJobs({ favorite: "1" })}><strong>{number(summary.review.favorite_jobs)}</strong><span>Your favorites <Arrow /></span></button>
      </section>
    </>}
    {!loading && <CollectionSnapshot run={latestRun} error={runError} onOpenJobs={() => openJobs({}, false)} />}
    {summary?.collection_validation && <CollectionEvidence evidence={summary.collection_validation} onReview={(filter) => openJobs(
      filter === "flagged" ? { flagged: "1", archived: "include" }
        : { relevance_outcome: filter, archived: filter === "auto_archived" ? "only" : "" }, false,
    )} />}
    {summary && <p className="market-endnote">Counts describe your collected LinkedIn sample. They are not estimates of the entire job market. Latest sighting: {formatDate(freshness?.last_collected_at, true)}.</p>}
  </div>;
};
export default SummaryPage;
