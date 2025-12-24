import React, { useEffect, useState } from "react";
import { listRuns } from "../api";
import type { CliMode, RunSummary } from "../types";

interface RecentRunsProps {
  className?: string;
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

const RecentRuns: React.FC<RecentRunsProps> = ({ className }) => {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setRuns(await listRuns(5));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load runs.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const wrapperClassName = ["panel", "recent-runs", className].filter(Boolean).join(" ");

  return (
    <section className={wrapperClassName}>
      <div className="header">
        <div>
          <p className="label">Recent runs</p>
          <h3>Latest activity</h3>
        </div>
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
    </section>
  );
};

export default RecentRuns;
