import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import JobDetail from "./components/JobDetail";
import JobTable, { JobTableHandle } from "./components/JobTable";
import RecentRuns from "./components/RecentRuns";
import RunPane from "./components/RunPane";
import SummaryPage from "./components/SummaryPage";
import { API_BASE, fetchStats } from "./api";
import type { Job, JobStats, RunStatus } from "./types";
import { readTextParam, replaceSearchParams } from "./urlState";

type ViewMode = "dashboard" | "summary";

const App: React.FC = () => {
  const [stats, setStats] = useState<JobStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>(() => (readTextParam("view") === "dashboard" ? "dashboard" : "summary"));
  const [dataRefreshToken, setDataRefreshToken] = useState(0);
  const [now, setNow] = useState(new Date());
  const tableRef = useRef<JobTableHandle | null>(null);
  const completedRunIdsRef = useRef(new Set<string>());

  useEffect(() => {
    replaceSearchParams({ view: viewMode });
  }, [viewMode]);

  useEffect(() => {
    let cancelled = false;

    const loadStats = async () => {
      try {
        const data = await fetchStats();
        if (!cancelled) {
          setStats(data);
          setStatsError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setStatsError("Could not load stats (check API is up and token matches).");
        }
      }
    };

    loadStats();
    const nowTimer = window.setInterval(() => setNow(new Date()), 30000);
    const statsTimer = window.setInterval(loadStats, 30000);

    return () => {
      cancelled = true;
      window.clearInterval(nowTimer);
      window.clearInterval(statsTimer);
    };
  }, [dataRefreshToken]);

  const formattedNow = useMemo(
    () => new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(now),
    [now]
  );
  const freshness = stats?.collection_freshness;
  const formatCollectionAge = (ageDays?: number | null) => {
    if (ageDays === null || ageDays === undefined) return "unknown age";
    if (ageDays < 1) return `${Math.max(1, Math.round(ageDays * 24))}h ago`;
    return `${Math.floor(ageDays)}d ago`;
  };
  const formatTimestamp = (value?: string | null) =>
    value
      ? new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
      : null;

  const handleArchiveChanged = (clearSelection = true) => {
    if (clearSelection) {
      setSelectedJob(null);
    }
    tableRef.current?.reload?.(clearSelection);
  };

  const handleAnnotationSaved = () => {
    tableRef.current?.reload?.(false);
  };

  const handleRunCompleted = useCallback((run: RunStatus) => {
    if (completedRunIdsRef.current.has(run.run_id)) {
      return;
    }
    completedRunIdsRef.current.add(run.run_id);
    setDataRefreshToken((token) => token + 1);
  }, []);

  const handleOpenDashboard = useCallback(() => {
    // Intelligence is calculated from active jobs. Do not let an archived-only
    // filter from an earlier dashboard session trap any of its drill-downs.
    replaceSearchParams({ archived: "" });
    setViewMode("dashboard");
  }, []);

  return (
    <div className="page">
      <header className="hero">
        <div className="hero-left">
          <h1>Job Informer</h1>
          <div className="hero-meta">
            <span className="eyebrow">{formattedNow}</span>
            <p className="muted small">Scrape, parse, and keep an eye on the database footprint.</p>
          </div>
        </div>
        <div className="hero-actions">
          <div className="badge">
            <span className="dot" />
            API {API_BASE}
          </div>
          <div className="view-toggle">
            <button
              type="button"
              className={viewMode === "dashboard" ? "active" : ""}
              aria-pressed={viewMode === "dashboard"}
              onClick={() => setViewMode("dashboard")}
            >
              Dashboard
            </button>
            <button
              type="button"
              className={viewMode === "summary" ? "active" : ""}
              aria-pressed={viewMode === "summary"}
              onClick={() => setViewMode("summary")}
            >
              Intelligence
            </button>
          </div>
        </div>
      </header>

      {statsError && (
        <div className="stat warning">
          <p className="label">Stats</p>
          <p className="value">{statsError}</p>
        </div>
      )}

      {stats && viewMode === "dashboard" && (
        <section
          className={`collection-freshness app-freshness ${freshness?.status || "empty"}`}
          role={freshness?.status === "stale" || freshness?.status === "empty" ? "alert" : "status"}
        >
          <div>
            <p className="label">Collection freshness</p>
            <h3>
              {freshness?.last_collected_at
                ? `Last observation ${formatCollectionAge(freshness.age_days)}`
                : "No collected observations yet"}
            </h3>
            <p className="small">
              {freshness?.status === "stale"
                ? "Market data is stale. Run Collect now before relying on current trends."
                : freshness?.status === "aging"
                  ? "Collection is getting old; another run is due soon."
                  : "Collection data is current."}
            </p>
          </div>
          <div className="freshness-meta">
            <span className={`freshness-state ${freshness?.status || "empty"}`}>{freshness?.status || "empty"}</span>
            <span className="muted tiny">
              {stats.collection_scheduler?.enabled
                ? `Automatic collection every ${stats.collection_scheduler.interval_hours}h`
                : "Automatic collection off"}
            </span>
            {stats.collection_scheduler?.last_successful_run_at && (
              <span className="muted tiny">
                Last successful run {formatTimestamp(stats.collection_scheduler.last_successful_run_at)}
              </span>
            )}
          </div>
        </section>
      )}

      <main className="layout" hidden={viewMode !== "dashboard"}>
        <div className="main">
          <RunPane className="highlight" onRunCompleted={handleRunCompleted} />
          {viewMode === "dashboard" && (
            <>
              <div className="jobs">
                <JobTable
                  ref={tableRef}
                  onSelect={setSelectedJob}
                  sources={stats?.sources_list || []}
                  companies={stats?.companies_list || []}
                  refreshToken={dataRefreshToken}
                />
                <JobDetail
                  job={selectedJob}
                  onArchiveChanged={handleArchiveChanged}
                  onAnnotationSaved={handleAnnotationSaved}
                />
              </div>
            </>
          )}
          <RecentRuns refreshToken={dataRefreshToken} />
        </div>
      </main>
      {viewMode === "summary" && (
        <SummaryPage
          key={`summary-${dataRefreshToken}`}
          className="summary-shell"
          onOpenDashboard={handleOpenDashboard}
        />
      )}
    </div>
  );
};

export default App;
