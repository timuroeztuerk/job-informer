import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDbSummary, getApiErrorMessage } from "../api";
import type { CountStat, DbSummary } from "../types";
import { replaceSearchParams } from "../urlState";

interface SummaryPageProps {
  className?: string;
  onOpenDashboard: () => void;
}

const formatDate = (value?: string | null, withTime = false): string => {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "n/a";
  return new Intl.DateTimeFormat("en", withTime
    ? { dateStyle: "medium", timeStyle: "short" }
    : { dateStyle: "medium" }).format(date);
};

const formatAge = (ageDays?: number | null): string => {
  if (ageDays === null || ageDays === undefined) return "No observations yet";
  if (ageDays < 1) {
    const hours = Math.max(1, Math.round(ageDays * 24));
    return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(ageDays);
  return `${days} day${days === 1 ? "" : "s"} ago`;
};

const Distribution: React.FC<{
  title: string;
  subtitle: string;
  items: CountStat[];
  onSelect: (name: string) => void;
}> = ({ title, subtitle, items, onSelect }) => {
  const peak = useMemo(() => Math.max(...items.map((item) => item.count), 1), [items]);
  return (
    <section className="intel-panel">
      <div className="insight-head">
        <p className="label">{title}</p>
        <p className="muted tiny">{subtitle}</p>
      </div>
      {items.length ? (
        <div className="intel-list">
          {items.map((item) => (
            <button key={item.name} type="button" className="intel-list-item interactive" onClick={() => onSelect(item.name)}>
              <div className="intel-list-copy">
                <span className="name">{item.name}</span>
                <span className="muted tiny">{item.count.toLocaleString()} ({(item.percentage || 0).toFixed(1)}%)</span>
              </div>
              <div className="intel-bar"><span style={{ width: `${Math.max(8, (item.count / peak) * 100)}%` }} /></div>
            </button>
          ))}
        </div>
      ) : <p className="muted small">No collected jobs yet.</p>}
    </section>
  );
};

const SummaryPage: React.FC<SummaryPageProps> = ({ className, onOpenDashboard }) => {
  const [summary, setSummary] = useState<DbSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await fetchDbSummary());
    } catch (reason) {
      setError(getApiErrorMessage(reason, "Could not load intelligence from the API."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const openJobs = useCallback((params: Record<string, string>) => {
    replaceSearchParams({
      view: "dashboard",
      search: "",
      location: "",
      source: "",
      company: "",
      role: "",
      query_group: "",
      from: "",
      to: "",
      sort: "",
      archived: "",
      job: "",
      page: "",
      ...params,
    });
    onOpenDashboard();
  }, [onOpenDashboard]);

  const freshness = summary?.collection_freshness;
  const freshnessTitle = freshness?.status === "fresh"
    ? "Collection is current"
    : freshness?.status === "aging"
      ? "Collection is getting old"
      : freshness?.status === "stale"
        ? "Collection is stale"
        : "No collection yet";

  return (
    <div className={["summary-page", className].filter(Boolean).join(" ")}>
      <section className="panel intro intelligence-hero">
        <div className="intro-row intelligence-hero-row">
          <div>
            <p className="label">Intelligence</p>
            <h2>Current job snapshot</h2>
            <p className="muted">Broad coverage only: volume, freshness, companies, and locations.</p>
          </div>
          <button className="ghost" type="button" onClick={reload} disabled={loading}>Refresh</button>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {loading && !summary && <div className="muted">Loading intelligence…</div>}
      </section>

      {summary && (
        <>
          <section className={`collection-freshness ${freshness?.status || "empty"}`} role={freshness?.status === "stale" ? "alert" : "status"}>
            <div>
              <p className="label">Collection freshness</p>
              <h3>{freshnessTitle}</h3>
              <p className="small">
                {freshness?.last_collected_at
                  ? `Latest LinkedIn observation: ${formatDate(freshness.last_collected_at, true)} (${formatAge(freshness.age_days)}).`
                  : "Run Collect now to create the first snapshot."}
              </p>
            </div>
            <span className={`freshness-state ${freshness?.status || "empty"}`}>{freshness?.status || "empty"}</span>
          </section>

          <section className="intel-metrics" aria-label="Current collection totals">
            <div className="intel-card"><p className="label">Active jobs</p><p className="intel-value">{summary.totals.total_jobs.toLocaleString()}</p><p className="muted tiny">Jobs available for review</p></div>
            <div className="intel-card"><p className="label">Added in 7 days</p><p className="intel-value">{summary.totals.recent_jobs_7_days.toLocaleString()}</p><p className="muted tiny">Based on first collection time</p></div>
            <div className="intel-card"><p className="label">Archived</p><p className="intel-value">{summary.totals.archived_jobs.toLocaleString()}</p><p className="muted tiny">Filtered or manually set aside</p></div>
            <div className="intel-card"><p className="label">Coverage since</p><p className="intel-value compact">{formatDate(summary.totals.date_range.earliest)}</p><p className="muted tiny">Oldest active posting</p></div>
          </section>

          <div className="intel-grid two-column">
            <Distribution
              title="Top companies"
              subtitle="Share of the active collection. Select one to inspect its jobs."
              items={summary.top_companies}
              onSelect={(company) => openJobs({ company })}
            />
            <Distribution
              title="Top locations"
              subtitle="Broad location mix. Select one to inspect its jobs."
              items={summary.top_locations}
              onSelect={(location) => openJobs({ location })}
            />
            <Distribution
              title="Target role families"
              subtitle="Current classified targets. Select one to inspect its jobs."
              items={summary.role_families || []}
              onSelect={(role) => openJobs({ role })}
            />
            <Distribution
              title="Query groups"
              subtitle="Search paths can overlap. Select one to inspect everything it found."
              items={summary.query_groups || []}
              onSelect={(query_group) => openJobs({ query_group })}
            />
          </div>
        </>
      )}
    </div>
  );
};

export default SummaryPage;
