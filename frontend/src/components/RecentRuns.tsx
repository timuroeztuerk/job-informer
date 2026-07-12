import React, { useEffect, useRef, useState } from "react";
import { listRuns } from "../api";
import type { CliMode, RunSummary } from "../types";

interface RecentRunsProps {
  className?: string;
  refreshToken?: number;
}

const modeLabel = (mode: CliMode) => {
  switch (mode) {
    case "run-once":
      return "Run";
    case "reset-ai-purge":
      return "Reset AI purge";
    case "parse-descriptions":
      return "Parse descriptions";
    case "db-summary":
      return "DB summary";
    case "test":
      return "Test";
    case "purge":
      return "Purge (rules + AI)";
    default:
      return mode;
  }
};

const modeClass = (mode: CliMode) => `mode-${mode.replace(/[^a-z]/g, "-")}`;

const formatDate = (value?: string | null) => {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
};

const RecentRuns: React.FC<RecentRunsProps> = ({ className, refreshToken = 0 }) => {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadRequestRef = useRef(0);

  const load = async () => {
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    setError(null);
    try {
      const nextRuns = await listRuns(5);
      if (loadRequestRef.current === requestId) {
        setRuns(nextRuns);
      }
    } catch (err) {
      if (loadRequestRef.current === requestId) {
        setError(err instanceof Error ? err.message : "Failed to load runs.");
      }
    } finally {
      if (loadRequestRef.current === requestId) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadRequestRef.current += 1;
    };
  }, [refreshToken]);

  const wrapperClassName = ["panel", "recent-runs", className].filter(Boolean).join(" ");

  return (
    <details className={`${wrapperClassName} operator-drawer`}>
      <summary className="operator-drawer-toggle">
        <div>
          <p className="label">Operator drawer</p>
          <h3>Recent runs</h3>
        </div>
        <span className="muted tiny">{runs.length ? `${runs.length} recent` : loading ? "Loading" : "Closed by default"}</span>
      </summary>

      <div className="header compact">
        <p className="muted small">Latest activity</p>
        <button className="ghost" type="button" onClick={load} disabled={loading}>
          Refresh
        </button>
      </div>

      {error ? (
        <div className="error">{error}</div>
      ) : loading ? (
        <div className="muted">Loading…</div>
      ) : !runs.length ? (
        <div className="muted">No runs yet.</div>
      ) : (
        <div className="list">
          {runs.map((run) => (
            <div key={run.run_id} className="row">
              <div className="top">
                <span className={`pill small mode ${modeClass(run.mode)}`}>{modeLabel(run.mode)}</span>
                <span className={`pill small ${run.status}`}>{run.status}</span>
              </div>
              <p className="muted small">{formatDate(run.started_at)}</p>
              <p className="muted tiny">{run.run_id.slice(0, 10)}</p>
            </div>
          ))}
        </div>
      )}
    </details>
  );
};

export default RecentRuns;
