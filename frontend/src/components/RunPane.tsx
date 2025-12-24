import React, { useEffect, useMemo, useRef, useState } from "react";
import { getRunStatus, startRun } from "../api";
import type { CliMode, RunStatus } from "../types";

interface RunPaneProps {
  className?: string;
}

interface QuickAction {
  mode: CliMode;
  title: string;
  description: string;
  subhead: string;
}

const quickActions: QuickAction[] = [
  {
    mode: "run-once",
    title: "Scrape new jobs",
    description: "Kick off the default job search using the configured keywords and locations.",
    subhead: "Scraper",
  },
  {
    mode: "purge",
    title: "Clean database",
    description: "Apply rule-based filters, then run the AI purge for noisy postings.",
    subhead: "Hygiene",
  },
  {
    mode: "parse-descriptions",
    title: "Parse descriptions",
    description: "Fetch any missing descriptions and parse them with the LLM.",
    subhead: "LLM",
  },
  {
    mode: "reset-ai-purge",
    title: "Reset AI purge",
    description: "Clear AI purge flags so you can re-run the smart filter.",
    subhead: "Maintenance",
  },
  {
    mode: "refetch-titles",
    title: "Fix masked titles",
    description: "Revisit stored job URLs to replace ******** titles/companies with the real names.",
    subhead: "Cleanup",
  },
];

const RunPane: React.FC<RunPaneProps> = ({ className }) => {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMode, setLoadingMode] = useState<CliMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollHandle = useRef<number | null>(null);
  const runIdRef = useRef("");

  const currentRunId = useMemo(() => status?.run_id || "", [status]);

  const modeLabel = (mode: CliMode) => {
    switch (mode) {
      case "run-once":
        return "Run";
      case "reset-ai-purge":
        return "Reset AI Purge";
      case "parse-descriptions":
        return "Descriptions";
      case "purge":
        return "Purge";
      case "refetch-titles":
        return "Refetch titles";
      default:
        return mode;
    }
  };

  const modeClass = (mode: CliMode) => `mode-${mode.replace(/[^a-z]/g, "-")}`;

  const stopPolling = () => {
    if (pollHandle.current) {
      window.clearInterval(pollHandle.current);
      pollHandle.current = null;
    }
  };

  const refreshStatus = async () => {
    const runId = runIdRef.current;
    if (!runId) return;
    try {
      const nextStatus = await getRunStatus(runId);
      setStatus(nextStatus);
      if (nextStatus.status !== "running") {
        stopPolling();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load status.");
      stopPolling();
    }
  };

  const startPolling = () => {
    stopPolling();
    pollHandle.current = window.setInterval(refreshStatus, 1500);
  };

  const startCliRun = async (mode: CliMode) => {
    setLoading(true);
    setLoadingMode(mode);
    setError(null);
    try {
      const nextStatus = await startRun({ mode });
      runIdRef.current = nextStatus.run_id;
      setStatus(nextStatus);
      startPolling();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start run.");
    } finally {
      setLoading(false);
      setLoadingMode(null);
    }
  };

  useEffect(() => {
    runIdRef.current = status?.run_id || "";
  }, [status?.run_id]);

  useEffect(() => () => stopPolling(), []);

  const wrapperClassName = ["panel", "run-pane", className].filter(Boolean).join(" ");

  return (
    <section className={wrapperClassName}>
      <div className="header">
        <div>
          <p className="label">Run scraper</p>
        </div>
        <div className="header-actions">
          <button className="ghost" type="button" onClick={refreshStatus} disabled={!currentRunId}>
            Refresh status
          </button>
        </div>
      </div>

      <div className="quick-actions">
        <div className="qa-head">
          <div>
            <p className="label">Shortcuts</p>
            <p className="hint">Launch any command from the backend.</p>
          </div>
          <span className="hint subtle" />
        </div>
        <div className="qa-grid">
          {quickActions.map((action) => (
            <button
              key={action.mode}
              type="button"
              className="qa-card"
              disabled={loading}
              onClick={() => startCliRun(action.mode)}
            >
              <div className="qa-row">
                <span className={`pill small mode ${modeClass(action.mode)}`}>{modeLabel(action.mode)}</span>
                <span className={`hint ${loadingMode === action.mode ? "bold" : ""}`}>
                  {loadingMode === action.mode ? "Starting…" : action.subhead}
                </span>
              </div>
              <p className="qa-title">{action.title}</p>
              <p className="qa-desc">{action.description}</p>
            </button>
          ))}
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {status && (
        <div className="status">
          <div className="status-row">
            <div>
              <p className="label">Run ID</p>
              <div className="id-row">
                <code>{status.run_id}</code>
                <span className={`pill small mode ${modeClass(status.mode)}`}>{modeLabel(status.mode)}</span>
              </div>
            </div>
            <span className={`pill ${status.status}`}>{status.status}</span>
          </div>
          <p className="label">Log tail</p>
          <pre>{status.log_tail || "Waiting for output..."}</pre>
        </div>
      )}
    </section>
  );
};

export default RunPane;
