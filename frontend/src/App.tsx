import React, { useEffect, useMemo, useRef, useState } from "react";
import JobDetail from "./components/JobDetail";
import JobTable, { JobTableHandle } from "./components/JobTable";
import RunPane from "./components/RunPane";
import SummaryPage from "./components/SummaryPage";
import { fetchStats } from "./api";
import type { Job, JobStats } from "./types";

type ViewMode = "dashboard" | "summary";

const App: React.FC = () => {
  const [stats, setStats] = useState<JobStats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("dashboard");
  const [now, setNow] = useState(new Date());
  const tableRef = useRef<JobTableHandle | null>(null);
  const apiBase = import.meta.env.VITE_API_BASE || "http://localhost:8000";

  useEffect(() => {
    let cancelled = false;

    const loadStats = async () => {
      try {
        const data = await fetchStats();
        if (!cancelled) {
          setStats(data);
        }
      } catch (err) {
        if (!cancelled) {
          setStatsError("Could not load stats (check API is up and token matches).");
        }
      }
    };

    loadStats();
    const timer = window.setInterval(() => setNow(new Date()), 30000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const formattedNow = useMemo(
    () => new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(now),
    [now]
  );

  const handleDeleted = () => {
    setSelectedJob(null);
    tableRef.current?.reload?.(true);
  };

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
            API {apiBase}
          </div>
          <div className="view-toggle">
            <button
              type="button"
              className={viewMode === "dashboard" ? "active" : ""}
              onClick={() => setViewMode("dashboard")}
            >
              Dashboard
            </button>
            <button
              type="button"
              className={viewMode === "summary" ? "active" : ""}
              onClick={() => setViewMode("summary")}
            >
              Summary
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

      {viewMode === "dashboard" ? (
        <main className="layout">
          <div className="main">
            <RunPane className="highlight" />
            <div className="jobs">
              <JobTable
                ref={tableRef}
                onSelect={setSelectedJob}
                sources={stats?.sources_list || []}
                companies={stats?.companies_list || []}
              />
              <JobDetail job={selectedJob} onDeleted={handleDeleted} />
            </div>
          </div>
        </main>
      ) : (
        <SummaryPage className="summary-shell" />
      )}
    </div>
  );
};

export default App;
