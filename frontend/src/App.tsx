import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import JobDetail from "./components/JobDetail";
import JobTable, { JobTableHandle } from "./components/JobTable";
import RunPane from "./components/RunPane";
import SummaryPage from "./components/SummaryPage";
import DescriptionQueue from "./components/DescriptionQueue";
import ExtractionQueue from "./components/ExtractionQueue";
import { API_BASE, fetchStats, getApiErrorMessage } from "./api";
import type { Job, JobFlag, JobStats, RunStatus } from "./types";
import { readTextParam, replaceSearchParams } from "./urlState";

type ViewMode = "dashboard" | "summary";
type ApiConnectionStatus = "checking" | "connected" | "disconnected";

const App: React.FC = () => {
  const [stats, setStats] = useState<JobStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [apiConnectionStatus, setApiConnectionStatus] = useState<ApiConnectionStatus>("checking");
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    const requestedView = readTextParam("view");
    return requestedView === "summary" ? requestedView : "dashboard";
  });
  const [dataRefreshToken, setDataRefreshToken] = useState(0);
  const [descriptionRefreshToken, setDescriptionRefreshToken] = useState(0);
  const handleDescriptionQueueChanged = useCallback(() => setDescriptionRefreshToken((token) => token + 1), []);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<RunStatus | null>(null);
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
          setApiConnectionStatus("connected");
        }
      } catch (err) {
        if (!cancelled) {
          setStatsError(getApiErrorMessage(err, "Could not load stats from the API."));
          setApiConnectionStatus("disconnected");
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
  const freshnessLabel = freshness?.last_collected_at
    ? `LinkedIn updated ${formatCollectionAge(freshness.age_days)}`
    : "LinkedIn not collected yet";
  const handleArchiveChanged = (clearSelection = true) => {
    if (clearSelection) {
      setSelectedJob(null);
    }
    tableRef.current?.reload?.(clearSelection);
  };
  const handleFavoriteChanged = (jobId: string, isFavorite: boolean) => {
    setSelectedJob((current) =>
      current?.job_id === jobId ? { ...current, is_favorite: isFavorite } : current
    );
    tableRef.current?.reload?.(false);
  };
  const handleFlagChanged = (flag: JobFlag) => {
    setSelectedJob((current) => current?.job_id === flag.job_id ? { ...current, ...flag } : current);
    tableRef.current?.reload?.(false);
  };

  const handleRunCompleted = useCallback((run: RunStatus) => {
    if (completedRunIdsRef.current.has(run.run_id)) {
      return;
    }
    completedRunIdsRef.current.add(run.run_id);
    setDataRefreshToken((token) => token + 1);
    setActionFeedback(
      run.status === "succeeded"
        ? `Run complete${run.metrics ? ` · ${run.metrics.new} new · ${run.metrics.archived} filtered` : ""}.`
        : `Run ${run.status}. Try again or inspect the local backend log if needed.`
    );
  }, []);

  const handleOpenDashboard = useCallback(() => {
    // Each Intelligence drill-down supplies its own complete filter scope.
    setViewMode("dashboard");
  }, []);

  const isCollectionRunning = activeRun?.mode === "run-once" && (activeRun.status === "starting" || activeRun.status === "running");

  return (
    <div className="page">
      <header className="hero">
        <div className="hero-left">
          <h1>Job Informer</h1>
          <div className="hero-meta">
            <span className="eyebrow">{formattedNow}</span>
            <p className="muted small">Collect LinkedIn jobs and review the local archive.</p>
          </div>
        </div>
        <div className="hero-actions">
          <div
            className="badge connection-summary"
            role="status"
            aria-live="polite"
            title={`API ${API_BASE}${freshness?.last_collected_at ? ` · Last LinkedIn observation ${freshness.last_collected_at}` : ""}`}
          >
            <span className={`dot ${apiConnectionStatus}`} />
            <span>API {apiConnectionStatus}</span>
            {apiConnectionStatus === "connected" && (
              <>
                <span className="status-separator" aria-hidden="true">·</span>
                <span className={`freshness-inline ${freshness?.status || "empty"}`}>{freshnessLabel}</span>
              </>
            )}
          </div>
          <div className="view-toggle">
            <button
              type="button"
              className={viewMode === "dashboard" ? "active" : ""}
              aria-pressed={viewMode === "dashboard"}
              onClick={() => setViewMode("dashboard")}
            >
              Jobs
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
        <div className="stat warning" role="alert">
          <p className="label">API connection</p>
          <p className="value">{statsError}</p>
          <button
            className="ghost sm"
            type="button"
            onClick={() => {
              setApiConnectionStatus("checking");
              setDataRefreshToken((token) => token + 1);
            }}
          >
            Retry connection
          </button>
        </div>
      )}

      {actionFeedback && (
        <div className="app-action-feedback" role="status" aria-live="polite">
          <span>{actionFeedback}</span>
          <button className="ghost sm" type="button" onClick={() => setActionFeedback(null)}>
            Dismiss
          </button>
        </div>
      )}

      {isCollectionRunning && viewMode !== "dashboard" && (
        <section className="collection-live-banner" role="status" aria-live="polite">
          <div>
            <p className="label">Collection in progress</p>
            <p className="small">
              {activeRun.status === "starting" ? "Preparing the collection run." : "Collecting jobs; compact progress is available on Jobs."}
            </p>
          </div>
          <button className="ghost" type="button" onClick={() => setViewMode("dashboard")}>
            View live progress
          </button>
        </section>
      )}

      {stats?.database?.total_jobs === 0 && (
        <div className="stat warning" role="alert">
          <p className="label">Database</p>
          <p className="value">Connected to an empty database.</p>
          <p className="muted small">
            Instance {stats.database.instance_name} is using {stats.database.db_path}. Check the Docker data mount before collecting new jobs.
          </p>
        </div>
      )}

      <main className="layout" hidden={viewMode !== "dashboard"}>
        <div className="main">
          <RunPane
            onRunCompleted={handleRunCompleted}
            onRunStatusChange={setActiveRun}
          />
          {viewMode === "dashboard" && (
            <>
              <DescriptionQueue refreshToken={descriptionRefreshToken} onChanged={handleDescriptionQueueChanged} />
              <ExtractionQueue onChanged={handleDescriptionQueueChanged} />
              <div className="jobs">
                <JobTable
                  ref={tableRef}
                  onSelect={setSelectedJob}
                  companies={stats?.companies_list || []}
                  roleFamilies={stats?.role_families_list || []}
                  queryGroups={stats?.query_groups_list || []}
                  refreshToken={dataRefreshToken}
                />
                <JobDetail
                  job={selectedJob}
                  onArchiveChanged={handleArchiveChanged}
                  onFavoriteChanged={handleFavoriteChanged}
                  onFlagChanged={handleFlagChanged}
                  onActionFeedback={setActionFeedback}
                  descriptionRefreshToken={descriptionRefreshToken}
                  onDescriptionQueueChanged={handleDescriptionQueueChanged}
                />
              </div>
            </>
          )}
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
