import React, { useEffect, useMemo, useRef, useState } from "react";
import { getRunStatus, startRun } from "../api";
import type { CliMode, RunStatus } from "../types";

interface RunPaneProps {
  className?: string;
  onRunCompleted?: (run: RunStatus) => void;
}

const ACTIVE_RUN_STORAGE_KEY = "job-informer.active-run";

interface QuickAction {
  mode: CliMode;
  title: string;
  description: string;
  subhead: string;
}

const quickActions: QuickAction[] = [
  {
    mode: "run-once",
    title: "Collect now",
    description: "Run the complete collection pipeline using the configured keywords and locations.",
    subhead: "Primary",
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

const RunPane: React.FC<RunPaneProps> = ({ className, onRunCompleted }) => {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMode, setLoadingMode] = useState<CliMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollHandle = useRef<number | null>(null);
  const logTailRef = useRef<HTMLPreElement | null>(null);
  const runIdRef = useRef("");
  const pollErrorStreak = useRef(0);
  const statusRequestInFlight = useRef(false);
  const notifiedRunIds = useRef(new Set<string>());
  const onRunCompletedRef = useRef(onRunCompleted);

  useEffect(() => {
    onRunCompletedRef.current = onRunCompleted;
  }, [onRunCompleted]);

  const currentRunId = useMemo(() => status?.run_id || "", [status]);
  const isRunActive = useMemo(() => status?.status === "running" || status?.status === "starting", [status]);
  const isActionDisabled = loading || isRunActive;

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
    if (pollHandle.current !== null) {
      window.clearInterval(pollHandle.current);
      pollHandle.current = null;
    }
  };

  const rememberActiveRun = (runId: string) => {
    try {
      window.sessionStorage.setItem(ACTIVE_RUN_STORAGE_KEY, runId);
    } catch {
      // A disabled storage backend should not stop a CLI run.
    }
  };

  const forgetActiveRun = (runId: string) => {
    try {
      if (window.sessionStorage.getItem(ACTIVE_RUN_STORAGE_KEY) === runId) {
        window.sessionStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
      }
    } catch {
      // A disabled storage backend should not stop terminal-state handling.
    }
  };

  const acceptStatus = (nextStatus: RunStatus) => {
    runIdRef.current = nextStatus.run_id;
    setStatus(nextStatus);
    if (nextStatus.status === "running") {
      rememberActiveRun(nextStatus.run_id);
      return;
    }

    stopPolling();
    forgetActiveRun(nextStatus.run_id);
    if (!notifiedRunIds.current.has(nextStatus.run_id)) {
      notifiedRunIds.current.add(nextStatus.run_id);
      onRunCompletedRef.current?.(nextStatus);
    }
  };

  const refreshStatus = async () => {
    const runId = runIdRef.current;
    if (!runId || statusRequestInFlight.current) return;
    statusRequestInFlight.current = true;
    try {
      const nextStatus = await getRunStatus(runId);
      if (runIdRef.current !== runId) return;
      pollErrorStreak.current = 0;
      setError(null);
      acceptStatus(nextStatus);
    } catch (err) {
      if (runIdRef.current !== runId) return;
      const streak = pollErrorStreak.current + 1;
      pollErrorStreak.current = streak;
      const message = err instanceof Error ? err.message : "Failed to load status.";
      if (streak >= 5) {
        setError(`${message} Polling disabled after repeated failures; use Refresh status manually.`);
        stopPolling();
      } else {
        setError(`${message} Retrying…`);
      }
    } finally {
      statusRequestInFlight.current = false;
    }
  };

  const startPolling = () => {
    stopPolling();
    pollHandle.current = window.setInterval(refreshStatus, 1000);
  };

  const startCliRun = async (mode: CliMode) => {
    if (isActionDisabled) {
      return;
    }

    pollErrorStreak.current = 0;
    setError(null);
    setLoading(true);
    setLoadingMode(mode);
    try {
      const nextStatus = await startRun({ mode });
      acceptStatus(nextStatus);
      if (nextStatus.status === "running") {
        startPolling();
        await refreshStatus();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start run.");
    } finally {
      setLoading(false);
      setLoadingMode(null);
    }
  };

  useEffect(() => {
    if (!status) return;
    if (logTailRef.current) {
      logTailRef.current.scrollTop = logTailRef.current.scrollHeight;
    }
  }, [status?.log_tail]);

  const visibleLogTail = (currentStatus: RunStatus | null): string => {
    if (!currentStatus || !currentStatus.log_tail) {
      return currentStatus?.status === "running"
        ? "Waiting for output..."
        : "Run completed without log output.";
    }
    return currentStatus.log_tail;
  };

  useEffect(() => {
    let activeRunId = "";
    try {
      activeRunId = window.sessionStorage.getItem(ACTIVE_RUN_STORAGE_KEY) || "";
    } catch {
      activeRunId = "";
    }
    if (activeRunId) {
      runIdRef.current = activeRunId;
      startPolling();
      void refreshStatus();
    }
    return () => stopPolling();
  }, []);

  const wrapperClassName = ["panel", "run-pane", className].filter(Boolean).join(" ");

  return (
    <section className={wrapperClassName}>
      <div className="header">
        <div>
          <p className="label">Collection and maintenance</p>
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
              disabled={isActionDisabled}
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
          {status.metrics && (
            <div className="run-metrics" aria-label="Run results">
              <span><strong>{status.metrics.observed}</strong> observed</span>
              <span><strong>{status.metrics.new}</strong> new</span>
              <span><strong>{status.metrics.archived}</strong> archived</span>
              <span><strong>{status.metrics.descriptions_fetched}</strong> described</span>
              <span><strong>{status.metrics.parsed}</strong> parsed</span>
            </div>
          )}
          {status.trigger === "scheduled" && <p className="muted tiny">Started by automatic collection.</p>}
          <p className="label">Log tail</p>
          <pre ref={logTailRef}>{visibleLogTail(status)}</pre>
        </div>
      )}
    </section>
  );
};

export default RunPane;
