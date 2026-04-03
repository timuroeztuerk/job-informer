import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDbSummary } from "../api";
import type { CountStat, DbSummary, TrendDelta, WeeklyJobCount } from "../types";
import { replaceSearchParams } from "../urlState";

interface SummaryPageProps {
  className?: string;
  onOpenDashboard: () => void;
}

interface MomentumCardProps {
  title: string;
  subtitle: string;
  items: TrendDelta[];
  onSelect?: (item: TrendDelta) => void;
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
}

const formatDate = (value?: string | null, withTime = false): string => {
  if (!value) return "n/a";
  const options: Intl.DateTimeFormatOptions = withTime
    ? { dateStyle: "medium", timeStyle: "short" }
    : { dateStyle: "medium" };
  return new Intl.DateTimeFormat("en", options).format(new Date(value));
};

const toDateInputValue = (value?: string | null): string => {
  if (!value) return "";
  const date = new Date(value);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const addDays = (value: string, days: number): string => {
  const date = new Date(`${value}T00:00:00`);
  date.setDate(date.getDate() + days);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};

const formatCount = (value?: number | null): string => (value === null || value === undefined ? "n/a" : value.toLocaleString());
const formatPct = (value?: number | null): string => (value === null || value === undefined ? "n/a" : `${value.toFixed(1)}%`);
const formatDecimal = (value?: number | null): string => (value === null || value === undefined ? "n/a" : value.toFixed(1));
const formatCurrency = (value?: number | null): string =>
  value === null || value === undefined ? "n/a" : `EUR ${Math.round(value).toLocaleString()}`;
const formatYears = (value?: number | null): string => (value === null || value === undefined ? "n/a" : `${value.toFixed(1)} yrs`);
const normalizeName = (value: string): string => value.replace(/_/g, " ");

const MetricCard: React.FC<MetricCardProps> = ({ label, value, detail }) => (
  <div className="intel-card">
    <p className="label">{label}</p>
    <p className="intel-value">{value}</p>
    <p className="muted tiny">{detail}</p>
  </div>
);

const DistributionPanel: React.FC<DistributionPanelProps> = ({ title, subtitle, items, onSelect }) => {
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
        <p className="muted small">No data yet.</p>
      )}
    </section>
  );
};

const MomentumCard: React.FC<MomentumCardProps> = ({ title, subtitle, items, onSelect }) => {
  return (
    <section className="intel-panel momentum-card">
      <div className="insight-head">
        <p className="label">{title}</p>
        <p className="muted tiny">{subtitle}</p>
      </div>
      {items.length ? (
        <div className="momentum-list">
          {items.map((item) => (
            <button
              key={item.name}
              type="button"
              className={`momentum-item ${onSelect ? "interactive" : ""}`}
              onClick={onSelect ? () => onSelect(item) : undefined}
              disabled={!onSelect}
            >
              <div>
                <span className="name">{normalizeName(item.name)}</span>
                <p className="muted tiny">
                  {item.recent_count} recent · {item.previous_count} previous
                </p>
              </div>
              <span className="delta up">+{item.delta}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="muted small">No rising signals in this window yet.</p>
      )}
    </section>
  );
};

const SummaryPage: React.FC<SummaryPageProps> = ({ className, onOpenDashboard }) => {
  const [summary, setSummary] = useState<DbSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = useMemo(() => summary?.parsed_insights || null, [summary]);
  const citySummary = useMemo(() => summary?.city_summary || null, [summary]);
  const trendSummary = useMemo(() => summary?.trend_summary || null, [summary]);
  const observationStats = useMemo(() => summary?.observation_stats || null, [summary]);
  const observationSummary = useMemo(() => summary?.observation_summary || null, [summary]);
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
  const salaryCoverage = useMemo(() => {
    if (!parsed?.total_records) return null;
    return (parsed.salary_eur.count / parsed.total_records) * 100;
  }, [parsed]);
  const seniorityMix = useMemo(() => parsed?.seniority_mix, [parsed]);
  const seniorSlice = useMemo(() => seniorityMix?.senior, [seniorityMix]);
  const nonSeniorSlice = useMemo(() => seniorityMix?.non_senior, [seniorityMix]);

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
  const weeklyPeak = useMemo(
    () => Math.max(...(trendSummary?.weekly_job_counts || []).map((entry) => entry.jobs), 1),
    [trendSummary]
  );
  const recentWindowLabel = useMemo(() => {
    if (!trendSummary) return "n/a";
    return `${formatDate(trendSummary.recent_window.start)} - ${formatDate(trendSummary.recent_window.end)}`;
  }, [trendSummary]);
  const previousWindowLabel = useMemo(() => {
    if (!trendSummary) return "n/a";
    return `${formatDate(trendSummary.previous_window.start)} - ${formatDate(trendSummary.previous_window.end)}`;
  }, [trendSummary]);
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

  const openWindow = useCallback(
    (start?: string | null, end?: string | null, inclusiveEndOffsetDays = 0) => {
      const from = toDateInputValue(start);
      const baseTo = toDateInputValue(end);
      const to = baseTo ? addDays(baseTo, inclusiveEndOffsetDays) : "";
      openDashboard({ from, to });
    },
    [openDashboard]
  );

  return (
    <div className={wrapperClass}>
      <section className="panel intro intelligence-hero">
        <div className="intro-row intelligence-hero-row">
          <div>
            <p className="label">Intelligence</p>
            <h2>Job market snapshot</h2>
            <p className="muted">
              Track market movement, source mix, and data trust without leaving the current dataset.
            </p>
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
          <MetricCard label="Recent jobs (7d)" value={formatCount(summary?.totals.recent_jobs_7_days)} detail="Fresh additions in the last week." />
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
            <p className="label">What changed recently</p>
            <h3>Movement in the market</h3>
          </div>
          {trendSummary && (
            <div className="window-stats">
              <button
                className="pill small tone interactive-pill"
                type="button"
                onClick={() => openWindow(trendSummary.recent_window.start, trendSummary.recent_window.end)}
              >
                Recent jobs: {trendSummary.recent_window.jobs.toLocaleString()}
              </button>
              <button
                className="pill small interactive-pill"
                type="button"
                onClick={() => openWindow(trendSummary.previous_window.start, trendSummary.previous_window.end, -1)}
              >
                Previous jobs: {trendSummary.previous_window.jobs.toLocaleString()}
              </button>
            </div>
          )}
        </div>
        {trendSummary ? (
          <>
            <p className="muted small window-copy">
              Recent window: {recentWindowLabel}. Compared against: {previousWindowLabel}.
            </p>
            <section className="intel-panel weekly-panel">
              <div className="insight-head">
                <p className="label">Weekly volume</p>
                <p className="muted tiny">{trendSummary.window_days}-day momentum window.</p>
              </div>
              <div className="weekly-list featured">
                {trendSummary.weekly_job_counts.map((entry: WeeklyJobCount) => {
                  const weekStart = toDateInputValue(entry.week_start);
                  const weekEnd = weekStart ? addDays(weekStart, 6) : "";
                  return (
                    <button
                      key={entry.week_start}
                      type="button"
                      className="weekly-row interactive"
                      onClick={() => openDashboard({ from: weekStart, to: weekEnd })}
                    >
                      <div className="weekly-meta">
                        <span className="name">{formatDate(entry.week_start)}</span>
                        <span className="muted tiny">
                          {entry.jobs.toLocaleString()} jobs · {entry.parsed_jobs.toLocaleString()} parsed
                        </span>
                      </div>
                      <div className="weekly-bar">
                        <span style={{ width: `${Math.max((entry.jobs / weeklyPeak) * 100, entry.jobs ? 6 : 0)}%` }} />
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>
            <div className="intel-grid">
              <MomentumCard
                title="Companies"
                subtitle="Companies showing up more often than before."
                items={trendSummary.momentum.companies}
                onSelect={(item) => openDashboard({ company: item.name })}
              />
              <MomentumCard
                title="Cities"
                subtitle="Locations with more recent openings."
                items={trendSummary.momentum.cities}
                onSelect={(item) => openDashboard({ location: item.name })}
              />
              <MomentumCard title="Skills" subtitle="Skills appearing more often in the last window." items={trendSummary.momentum.skills} />
              <MomentumCard title="Tools" subtitle="Platforms and tooling demand that is rising." items={trendSummary.momentum.tools} />
              <MomentumCard
                title="Languages"
                subtitle="Programming languages with upward momentum."
                items={trendSummary.momentum.programming_languages}
              />
            </div>
          </>
        ) : (
          <p className="muted small">No trend data yet.</p>
        )}
      </section>

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Market profile</p>
            <h3>Where the current dataset leans</h3>
          </div>
        </div>
        <div className="intel-grid">
          <DistributionPanel title="Source mix" subtitle="Share of jobs by source." items={top(sourceMix)} onSelect={(item) => openDashboard({ source: item.name })} />
          <DistributionPanel
            title="Top companies"
            subtitle="Most common employers in the current set."
            items={top(topCompanies)}
            onSelect={(item) => openDashboard({ company: item.name })}
          />
          <DistributionPanel
            title="Top cities"
            subtitle="Primary cities mentioned in listings."
            items={top(citySummary?.top_cities || [])}
            onSelect={(item) => openDashboard({ location: item.name })}
          />
          <DistributionPanel title="Remote options" subtitle="Parsed working model mix." items={top(parsed?.remote_options || [])} />
          <DistributionPanel title="Seniority" subtitle="Parsed role levels." items={top(parsed?.seniority_levels || [])} />
        </div>
      </section>

      <section className="panel intelligence-section">
        <div className="section-head">
          <div>
            <p className="label">Data trust and recurrence</p>
            <h3>Quality and repeat-sighting signals</h3>
          </div>
        </div>
        <div className="intel-grid two-up">
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
