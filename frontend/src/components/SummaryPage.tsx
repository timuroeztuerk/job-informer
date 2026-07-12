import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDbSummary } from "../api";
import type { CountStat, DbSummary, TrendDelta } from "../types";
import { replaceSearchParams } from "../urlState";

interface SummaryPageProps {
  className?: string;
  onOpenDashboard: () => void;
}

interface MetricCardProps {
  label: string;
  value: string;
  detail: string;
}

interface DistributionPanelProps {
  title: string;
  subtitle: string;
  items: CountStat[];
  onSelect?: (item: CountStat) => void;
  emptyMessage?: string;
}

interface MomentumPanelProps {
  title: string;
  items: TrendDelta[];
  onSelect?: (item: TrendDelta) => void;
}

const formatDate = (value?: string | null, withTime = false): string => {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "n/a";
  const options: Intl.DateTimeFormatOptions = withTime
    ? { dateStyle: "medium", timeStyle: "short" }
    : { dateStyle: "medium" };
  return new Intl.DateTimeFormat("en", options).format(date);
};

const formatCount = (value?: number | null): string => (value === null || value === undefined ? "n/a" : value.toLocaleString());
const formatPct = (value?: number | null): string => (value === null || value === undefined ? "n/a" : `${value.toFixed(1)}%`);
const formatDecimal = (value?: number | null): string => (value === null || value === undefined ? "n/a" : value.toFixed(1));
const normalizeName = (value: string): string => value.replace(/_/g, " ");
const formatAge = (ageDays?: number | null): string => {
  if (ageDays === null || ageDays === undefined) return "unknown age";
  if (ageDays < 1) return "today";
  const rounded = Math.floor(ageDays);
  return `${rounded} day${rounded === 1 ? "" : "s"} ago`;
};

const MetricCard: React.FC<MetricCardProps> = ({ label, value, detail }) => (
  <div className="intel-card">
    <p className="label">{label}</p>
    <p className="intel-value">{value}</p>
    <p className="muted tiny">{detail}</p>
  </div>
);

const DistributionPanel: React.FC<DistributionPanelProps> = ({
  title,
  subtitle,
  items,
  onSelect,
  emptyMessage = "No data yet.",
}) => {
  const peak = useMemo(() => Math.max(...items.map((item) => item.count), 1), [items]);

  return (
    <section className="intel-panel">
      <div className="insight-head">
        <p className="label">{title}</p>
        <p className="muted tiny">{subtitle}</p>
      </div>
      {items.length ? (
        <div className="intel-list">
          {items.map((item) => {
            const width = Math.max(8, item.percentage ?? (item.count / peak) * 100);
            const content = (
              <>
                <div className="intel-list-copy">
                  <span className="name">{normalizeName(item.name)}</span>
                  <span className="muted tiny">
                    {item.count.toLocaleString()}
                    {item.percentage !== undefined ? ` (${item.percentage.toFixed(1)}%)` : ""}
                  </span>
                </div>
                <div className="intel-bar">
                  <span style={{ width: `${width}%` }} />
                </div>
              </>
            );

            return onSelect ? (
              <button key={item.name} type="button" className="intel-list-item interactive" onClick={() => onSelect(item)}>
                {content}
              </button>
            ) : (
              <div key={item.name} className="intel-list-item">
                {content}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="muted small">{emptyMessage}</p>
      )}
    </section>
  );
};

const MomentumPanel: React.FC<MomentumPanelProps> = ({ title, items, onSelect }) => (
  <section className="intel-panel">
    <div className="insight-head">
      <p className="label">{title}</p>
      <p className="muted tiny">Positive change against the preceding window.</p>
    </div>
    {items.length ? (
      <div className="momentum-list">
        {items.map((item) => {
          const content = (
            <>
              <span className="name">{normalizeName(item.name)}</span>
              <span className="momentum-meta">
                <span className="muted tiny">{item.recent_count} vs {item.previous_count}</span>
                <span className="delta up">+{item.delta}</span>
              </span>
            </>
          );
          return onSelect ? (
            <button key={item.name} type="button" className="momentum-item interactive" onClick={() => onSelect(item)}>
              {content}
            </button>
          ) : (
            <div key={item.name} className="momentum-item">
              {content}
            </div>
          );
        })}
      </div>
    ) : (
      <p className="muted small">No positive movement in this window.</p>
    )}
  </section>
);

const SummaryPage: React.FC<SummaryPageProps> = ({ className, onOpenDashboard }) => {
  const [summary, setSummary] = useState<DbSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = useMemo(() => summary?.parsed_insights || null, [summary]);
  const citySummary = useMemo(() => summary?.city_summary || null, [summary]);
  const observationStats = useMemo(() => summary?.observation_stats || null, [summary]);
  const observationSummary = useMemo(() => summary?.observation_summary || null, [summary]);
  const freshness = useMemo(() => summary?.collection_freshness || null, [summary]);
  const fitSummary = useMemo(() => summary?.profile_fit_summary || null, [summary]);
  const parserTelemetry = useMemo(() => summary?.parser_telemetry || null, [summary]);
  const skillGapSummary = useMemo(() => summary?.skill_gap_summary || null, [summary]);
  const trend = useMemo(() => summary?.trend_summary || null, [summary]);
  const orphanedJobs = useMemo(
    () => summary?.parsed_descriptions_stats?.orphaned_parsed_descriptions ?? parsed?.orphaned_jobs ?? 0,
    [parsed, summary]
  );
  const historicalPayloads = useMemo(
    () => parsed?.historical_payloads ?? summary?.parsed_descriptions_stats?.total_parsed_descriptions ?? 0,
    [parsed, summary]
  );
  const totalJobs = summary?.totals.total_jobs || 0;
  const coveragePct = useMemo(() => {
    if (!totalJobs) return null;
    return ((parsed?.total_records || 0) / totalJobs) * 100;
  }, [parsed, totalJobs]);
  const coverageDisplay = useMemo(() => formatPct(coveragePct), [coveragePct]);
  const fitCoverage = useMemo(() => {
    if (!totalJobs || !fitSummary) return null;
    return (fitSummary.total_scored / totalJobs) * 100;
  }, [fitSummary, totalJobs]);
  const parserHealth = useMemo(() => {
    if (!parserTelemetry || parserTelemetry.attempts === 0) {
      return { label: "No telemetry", tone: "neutral", detail: "No parser calls have been recorded." };
    }
    if (
      parserTelemetry.jobs_with_current_failed_status > 0
      || (parserTelemetry.recent_success_rate !== null && parserTelemetry.recent_success_rate < 0.9)
    ) {
      return { label: "Needs attention", tone: "warning", detail: "Recent failures or unresolved parser jobs are present." };
    }
    if (parserTelemetry.recent_attempts === 0) {
      return { label: "Idle", tone: "neutral", detail: `No calls in the last ${parserTelemetry.recent_window_days} days.` };
    }
    return { label: "Healthy", tone: "healthy", detail: "Recent parser calls are succeeding." };
  }, [parserTelemetry]);
  const recentWeeks = useMemo(() => (trend?.weekly_job_counts || []).slice(-8), [trend]);
  const weeklyPeak = useMemo(() => Math.max(...recentWeeks.map((entry) => entry.jobs), 1), [recentWeeks]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchDbSummary();
      setSummary(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load summary.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const wrapperClass = ["summary-page", className].filter(Boolean).join(" ");
  const sourceMix = useMemo(
    () =>
      Object.entries(summary?.jobs_by_source || {})
        .map(([name, count]) => ({
          name,
          count,
          percentage: totalJobs ? (count / totalJobs) * 100 : 0,
        }))
        .sort((left, right) => right.count - left.count),
    [summary, totalJobs]
  );
  const topCompanies = useMemo(
    () =>
      Object.entries(summary?.top_companies || {})
        .map(([name, count]) => ({
          name,
          count,
          percentage: totalJobs ? (count / totalJobs) * 100 : 0,
        }))
        .sort((left, right) => right.count - left.count),
    [summary, totalJobs]
  );

  const top = (entries: CountStat[] = [], take = 6) => entries.slice(0, take);

  const openDashboard = useCallback(
    (nextParams: Record<string, string>) => {
      replaceSearchParams({
        view: "dashboard",
        search: "",
        location: "",
        source: "",
        company: "",
        status: "",
        priority: "",
        from: "",
        to: "",
        sort: "",
        job: "",
        page: "",
        ...nextParams,
      });
      onOpenDashboard();
    },
    [onOpenDashboard]
  );

  return (
    <div className={wrapperClass}>
      <section className="panel intro intelligence-hero">
        <div className="intro-row intelligence-hero-row">
          <div>
            <p className="label">Intelligence</p>
            <h2>Job market snapshot</h2>
            <p className="muted">Compact view of source mix, quality, and recurrence.</p>
          </div>
          <div className="intro-actions">
            <span className="pill small tone">Default home</span>
            <button className="ghost" type="button" onClick={reload} disabled={loading}>
              Refresh
            </button>
          </div>
        </div>

        {error && <div className="error">{error}</div>}
        {!error && loading && !summary && <div className="muted">Loading intelligence...</div>}
      </section>

      {summary && (
        <section
          className={`collection-freshness ${freshness?.status || "empty"}`}
          role={freshness?.status === "stale" ? "alert" : "status"}
        >
          <div>
            <p className="label">Collection freshness</p>
            <h3>
              {freshness?.status === "fresh" && "Collection is current"}
              {freshness?.status === "aging" && "Collection is getting old"}
              {freshness?.status === "stale" && "Collection is stale"}
              {(!freshness || freshness.status === "empty") && "No collected observations yet"}
            </h3>
            <p className="small">
              {freshness?.last_collected_at
                ? `Latest actual observation: ${formatDate(freshness.last_collected_at, true)} (${formatAge(freshness.age_days)}).`
                : "Run a collection before treating market signals as current."}
              {freshness?.status === "stale" && " Current trend windows stay anchored to today, so the archive cannot masquerade as live activity."}
            </p>
          </div>
          <div className="freshness-meta">
            <span className={`freshness-state ${freshness?.status || "empty"}`}>{freshness?.status || "empty"}</span>
            {freshness?.latest_scrape_at && (
              <span className="muted tiny">Latest scrape started {formatDate(freshness.latest_scrape_at, true)}</span>
            )}
          </div>
        </section>
      )}

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Snapshot</p>
            <h3>Current database posture</h3>
          </div>
          <p className="muted small">
            {summary?.totals.date_range.earliest
              ? `${formatDate(summary.totals.date_range.earliest)} - ${formatDate(summary.totals.date_range.latest)}`
              : "Date range n/a"}
          </p>
        </div>
        <div className="intel-grid four">
          <MetricCard label="Total jobs" value={formatCount(summary?.totals.total_jobs)} detail="All rows currently stored." />
          <MetricCard label="Recent jobs (7d)" value={formatCount(summary?.totals.recent_jobs_7_days)} detail="Collected in the current UTC 7-day period." />
          <MetricCard
            label="Parsed coverage"
            value={parsed ? `${formatCount(parsed.total_records)} / ${formatCount(summary?.totals.total_jobs)}` : "n/a"}
            detail={`Coverage ${coverageDisplay}`}
          />
          <MetricCard
            label="Repeat jobs"
            value={formatCount(observationStats?.repeat_jobs ?? observationSummary?.repeat_jobs)}
            detail="Jobs seen in more than one scrape."
          />
        </div>
      </section>

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Profile fit</p>
            <h3>Best matches for your profile</h3>
          </div>
          <button className="ghost" type="button" onClick={() => openDashboard({ sort: "fit_score_desc" })}>
            Review all by fit
          </button>
        </div>
        <div className="intel-grid four">
          <MetricCard
            label="Scored jobs"
            value={formatCount(fitSummary?.total_scored)}
            detail={`${formatPct(fitCoverage)} of active jobs scored.`}
          />
          <MetricCard label="Average fit" value={formatDecimal(fitSummary?.average_score)} detail={fitSummary?.profile_name || "Active fit profile."} />
          <MetricCard label="High fit" value={formatCount(fitSummary?.band_counts.high)} detail="Strongest profile matches." />
          <MetricCard label="Medium fit" value={formatCount(fitSummary?.band_counts.medium)} detail="Worth a quick operator review." />
        </div>
        <section className="intel-panel fit-panel">
          <div className="insight-head">
            <p className="label">Top jobs</p>
            <p className="muted tiny">Open a match directly in the dashboard.</p>
          </div>
          {fitSummary?.top_jobs.length ? (
            <div className="fit-job-list">
              {fitSummary.top_jobs.map((job) => (
                <button key={job.job_id} type="button" className="fit-job" onClick={() => openDashboard({ job: job.job_id })}>
                  <span className="fit-score">{job.score.toFixed(0)}</span>
                  <span className="fit-job-copy">
                    <span className="name">{job.title}</span>
                    <span className="muted tiny">{job.company}{job.reasons[0] ? ` · ${job.reasons[0]}` : ""}</span>
                  </span>
                  <span className={`fit-band ${job.band}`}>{normalizeName(job.band)}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted small">No fit scores yet. Parse and score jobs to populate this queue.</p>
          )}
        </section>
        <DistributionPanel
          title="Repeated personal skill gaps"
          subtitle={skillGapSummary?.jobs_with_gaps
            ? `Counted once per gap across ${skillGapSummary.jobs_with_gaps} active annotated jobs.`
            : "Aggregated from skill gaps in your active-job annotations."}
          items={skillGapSummary?.top_gaps || []}
          emptyMessage="No skill gaps have been annotated on active jobs yet."
        />
      </section>

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Parsed demand</p>
            <h3>Skills and technologies in the market</h3>
          </div>
          <p className="muted small">Based on the latest parse for each active job.</p>
        </div>
        <div className="intel-grid demand-grid">
          <DistributionPanel title="Skills" subtitle="Most requested capabilities." items={top(parsed?.skills || [])} />
          <DistributionPanel title="Tools" subtitle="Most mentioned platforms and tooling." items={top(parsed?.tools || [])} />
          <DistributionPanel title="Programming languages" subtitle="Most requested code languages." items={top(parsed?.programming_languages || [])} />
          <DistributionPanel title="Spoken languages" subtitle="Language requirements in parsed jobs." items={top(parsed?.languages || [])} />
        </div>
      </section>

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Momentum</p>
            <h3>What is rising now</h3>
          </div>
          <div className="window-stats">
            <span className="pill small">Current: {formatCount(trend?.recent_window.jobs)} jobs</span>
            <span className="pill small">Previous: {formatCount(trend?.previous_window.jobs)} jobs</span>
          </div>
        </div>
        <p className="muted tiny window-copy">
          Current UTC window: {formatDate(trend?.recent_window.start, true)} – {formatDate(trend?.recent_window.end, true)}.
          {" "}Compared with {formatDate(trend?.previous_window.start, true)} – {formatDate(trend?.previous_window.end, true)}.
        </p>
        {freshness?.status === "stale" && (
          <p className="trend-caveat">
            {trend?.recent_window.jobs
              ? "The current window contains older observations, but collection itself is stale; interpret movement cautiously."
              : "Signals are quiet because no observations fall in the current window; historical volume remains below."}
          </p>
        )}
        <div className="intel-grid momentum-grid">
          <MomentumPanel title="Skills" items={trend?.momentum.skills || []} />
          <MomentumPanel title="Tools" items={trend?.momentum.tools || []} />
          <MomentumPanel title="Programming languages" items={trend?.momentum.programming_languages || []} />
          <MomentumPanel title="Companies" items={trend?.momentum.companies || []} onSelect={(item) => openDashboard({ company: item.name })} />
          <MomentumPanel title="Cities" items={trend?.momentum.cities || []} onSelect={(item) => openDashboard({ location: item.name })} />
        </div>
        <section className="intel-panel weekly-panel">
          <div className="insight-head">
            <p className="label">Weekly job volume</p>
            <p className="muted tiny">Eight calendar weeks ending in the current UTC week; empty weeks remain visible.</p>
          </div>
          {recentWeeks.length ? (
            <div className="weekly-list featured">
              {recentWeeks.map((entry) => (
                <div key={entry.week_start} className="weekly-row">
                  <div className="weekly-meta">
                    <span className="name">Week of {formatDate(entry.week_start)}</span>
                    <span className="muted tiny">{entry.jobs} jobs · {entry.parsed_jobs} parsed</span>
                  </div>
                  <div className="weekly-bar"><span style={{ width: `${(entry.jobs / weeklyPeak) * 100}%` }} /></div>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted small">No weekly history yet.</p>
          )}
        </section>
      </section>

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Market profile</p>
            <h3>Current shape</h3>
          </div>
        </div>

        <section className="intel-panel market-strip">
          <div className="market-strip-column">
            <div className="insight-head">
              <p className="label">Source mix</p>
              <p className="muted tiny">Share of jobs by source.</p>
            </div>
            <div className="market-strip-list">
              {top(sourceMix, 4).map((item) => (
                <button key={item.name} type="button" className="market-strip-item" onClick={() => openDashboard({ source: item.name })}>
                  <span>{normalizeName(item.name)}</span>
                  <span className="muted tiny">{item.count.toLocaleString()}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="market-strip-column">
            <div className="insight-head">
              <p className="label">Top companies</p>
              <p className="muted tiny">Most common employers.</p>
            </div>
            <div className="market-strip-list">
              {top(topCompanies, 4).map((item) => (
                <button key={item.name} type="button" className="market-strip-item" onClick={() => openDashboard({ company: item.name })}>
                  <span>{normalizeName(item.name)}</span>
                  <span className="muted tiny">{item.count.toLocaleString()}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="market-strip-column">
            <div className="insight-head">
              <p className="label">Top cities</p>
              <p className="muted tiny">Primary cities mentioned.</p>
            </div>
            <div className="market-strip-list">
              {top(citySummary?.top_cities || [], 4).map((item) => (
                <button key={item.name} type="button" className="market-strip-item" onClick={() => openDashboard({ location: item.name })}>
                  <span>{normalizeName(item.name)}</span>
                  <span className="muted tiny">{item.count.toLocaleString()}</span>
                </button>
              ))}
            </div>
          </div>
        </section>

        <div className="intel-grid two-up">
          <DistributionPanel title="Remote options" subtitle="Parsed working model mix." items={top(parsed?.remote_options || [])} />
          <DistributionPanel title="Seniority" subtitle="Parsed role levels." items={top(parsed?.seniority_levels || [])} />
        </div>
      </section>

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Data trust and recurrence</p>
            <h3>Quality and repeat-sighting</h3>
          </div>
        </div>
        <div className="intel-grid trust-grid">
          <section className="intel-panel">
            <div className="insight-head">
              <p className="label">Parsed data quality</p>
              <p className="muted tiny">Coverage and payload hygiene for parsed descriptions.</p>
            </div>
            <div className="metric-grid">
              <div>
                <p className="muted tiny">Total parsed</p>
                <p className="metric-value">{formatCount(summary?.parsed_descriptions_stats?.total_parsed_descriptions)}</p>
              </div>
              <div>
                <p className="muted tiny">Active parsed</p>
                <p className="metric-value">{formatCount(summary?.parsed_descriptions_stats?.active_parsed_descriptions)}</p>
              </div>
              <div>
                <p className="muted tiny">Jobs with descriptions</p>
                <p className="metric-value">{formatCount(summary?.parsed_descriptions_stats?.jobs_with_descriptions)}</p>
              </div>
              <div>
                <p className="muted tiny">Orphaned payloads</p>
                <p className="metric-value">{formatCount(orphanedJobs)}</p>
              </div>
              <div>
                <p className="muted tiny">Historical payloads</p>
                <p className="metric-value">{formatCount(historicalPayloads)}</p>
              </div>
              <div>
                <p className="muted tiny">Coverage</p>
                <p className="metric-value">{coverageDisplay}</p>
              </div>
            </div>
          </section>

          <section className="intel-panel">
            <div className="insight-head">
              <p className="label">Observation and recurrence</p>
              <p className="muted tiny">How often jobs reappear and how long they stay active.</p>
            </div>
            <div className="metric-grid">
              <div>
                <p className="muted tiny">Total observations</p>
                <p className="metric-value">{formatCount(observationStats?.total_observations ?? observationSummary?.total_observations)}</p>
              </div>
              <div>
                <p className="muted tiny">Repeat jobs</p>
                <p className="metric-value">{formatCount(observationStats?.repeat_jobs ?? observationSummary?.repeat_jobs)}</p>
              </div>
              <div>
                <p className="muted tiny">Avg seen count</p>
                <p className="metric-value">{formatDecimal(observationStats?.average_seen_count ?? observationSummary?.average_seen_count)}</p>
              </div>
              <div>
                <p className="muted tiny">Max seen count</p>
                <p className="metric-value">{formatCount(observationStats?.max_seen_count ?? observationSummary?.max_seen_count)}</p>
              </div>
            </div>
            <div className="recurrence-actions">
              <button className="ghost" type="button" onClick={() => openDashboard({ sort: "seen_count_desc" })}>
                Most recurring
              </button>
              <button className="ghost" type="button" onClick={() => openDashboard({ sort: "last_seen_desc" })}>
                Recently seen again
              </button>
            </div>
          </section>

          <section className="intel-panel parser-health-panel">
            <div className="insight-head parser-health-head">
              <div>
                <p className="label">Parser health</p>
                <p className="muted tiny">Operational telemetry for structured-description parsing.</p>
              </div>
              <span className={`health-state ${parserHealth.tone}`}>{parserHealth.label}</span>
            </div>
            <p className="muted tiny parser-health-detail">{parserHealth.detail}</p>
            <div className="metric-grid">
              <div>
                <p className="muted tiny">Recent success</p>
                <p className="metric-value">
                  {parserTelemetry?.recent_success_rate === null || parserTelemetry?.recent_success_rate === undefined
                    ? "n/a"
                    : formatPct(parserTelemetry.recent_success_rate * 100)}
                </p>
              </div>
              <div>
                <p className="muted tiny">Recent calls</p>
                <p className="metric-value">{formatCount(parserTelemetry?.recent_attempts)}</p>
              </div>
              <div>
                <p className="muted tiny">Failed jobs</p>
                <p className="metric-value">{formatCount(parserTelemetry?.jobs_with_current_failed_status)}</p>
              </div>
              <div>
                <p className="muted tiny">Avg latency</p>
                <p className="metric-value">
                  {parserTelemetry?.average_latency_ms === null || parserTelemetry?.average_latency_ms === undefined
                    ? "n/a"
                    : `${(parserTelemetry.average_latency_ms / 1000).toFixed(1)}s`}
                </p>
              </div>
              <div>
                <p className="muted tiny">All attempts</p>
                <p className="metric-value">{formatCount(parserTelemetry?.attempts)}</p>
              </div>
              <div>
                <p className="muted tiny">Exhausted retries</p>
                <p className="metric-value">{formatCount(parserTelemetry?.exhausted_retries)}</p>
              </div>
            </div>
          </section>
        </div>

        <section className="intel-panel recurring-panel">
          <div className="insight-head">
            <p className="label">Top recurring jobs</p>
            <p className="muted tiny">Open a recurring posting directly in the dashboard.</p>
          </div>
          {observationSummary?.top_recurring_jobs?.length ? (
            <div className="recurring-list">
              {observationSummary.top_recurring_jobs.map((item) => (
                <button key={item.job_id} type="button" className="recurring-item" onClick={() => openDashboard({ job: item.job_id })}>
                  <div>
                    <p className="recurring-title">{item.title}</p>
                    <p className="muted tiny">{item.company}</p>
                  </div>
                  <div className="recurring-meta">
                    <span className="tag soft">Seen {item.seen_count}x</span>
                    <span className="tag soft">{item.active_days} active days</span>
                    <span className="tag soft">Last seen {formatDate(item.last_seen_at, true)}</span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted small">Recurrence tracking is not available yet.</p>
          )}
        </section>
      </section>
    </div>
  );
};

export default SummaryPage;
