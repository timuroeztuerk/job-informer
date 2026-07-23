import React, { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { getRunStatus, listRuns, startRun } from "../api";
import type { CliMode, RunMetrics, RunProgress, RunStatus } from "../types";

interface RunPaneProps {
  className?: string;
  onRunCompleted?: (run: RunStatus) => void;
  onRunStatusChange?: (run: RunStatus | null) => void;
}

export interface RunPaneHandle {
  startCollection: () => void;
}

const ACTIVE_RUN_STORAGE_KEY = "job-informer.active-run";

interface QuickAction {
  mode: CliMode;
  title: string;
  description: string;
  subhead: string;
  confirmMessage?: string;
}

const maintenanceActions: QuickAction[] = [
  {
    mode: "purge",
    title: "Clean database",
    description: "Archives postings matched by rule-based and AI noise filters.",
    subhead: "Hygiene",
    confirmMessage: "Clean the database now? Matching postings may be archived by rule-based and AI filters.",
  },
  {
    mode: "parse-descriptions",
    title: "Parse descriptions",
    description: "Fetches missing descriptions and sends them through the LLM parser.",
    subhead: "LLM",
  },
  {
    mode: "reset-ai-purge",
    title: "Reset AI purge",
    description: "Clears AI purge flags so previously checked jobs can be evaluated again.",
    subhead: "Maintenance",
    confirmMessage: "Reset all AI purge flags? Previously checked jobs will be eligible for AI filtering again.",
  },
  {
    mode: "refetch-titles",
    title: "Fix masked titles",
    description: "Revisits stored URLs to replace masked titles and company names.",
    subhead: "Cleanup",
  },
];

const isActiveStatus = (status?: RunStatus["status"]): boolean => status === "starting" || status === "running";

const formatElapsed = (startedAt: string, now: number): string => {
  const started = Date.parse(startedAt);
  if (Number.isNaN(started)) return "just started";
  const totalSeconds = Math.max(0, Math.floor((now - started) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes ? `${minutes}m ${seconds}s elapsed` : `${seconds}s elapsed`;
};

const fallbackProgressLabel = (status: RunStatus): string => {
  if (status.status === "starting") return "Preparing the run";
  if (status.status === "running") return "Collecting and recording live output";
  if (status.status === "succeeded") return "Run completed";
  return `Run ${status.status}`;
};

const sourceProgress = (progress?: RunProgress | null): { completed: number; total: number } | null => {
  if (!progress || progress.completed_sources == null || progress.total_sources == null || progress.total_sources <= 0) {
    return null;
  }
  return {
    completed: Math.min(Math.max(0, progress.completed_sources), progress.total_sources),
    total: progress.total_sources,
  };
};

const formatActivityTime = (timestamp: string): string => {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

const metricItems = (metrics?: RunMetrics | null) => [
  [metrics?.observed ?? 0, "observed"],
  [metrics?.new ?? 0, "new"],
  [metrics?.archived ?? 0, "archived"],
  [metrics?.descriptions_fetched ?? 0, "descriptions"],
  [metrics?.parsed ?? 0, "parsed"],
] as const;

const RunPane = forwardRef<RunPaneHandle, RunPaneProps>(({ className, onRunCompleted, onRunStatusChange }, ref) => {
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMode, setLoadingMode] = useState<CliMode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
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

  useEffect(() => {
    onRunStatusChange?.(status);
  }, [onRunStatusChange, status]);

  const currentRunId = useMemo(() => status?.run_id || "", [status]);
  const isRunActive = useMemo(() => isActiveStatus(status?.status), [status]);
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
    if (isActiveStatus(nextStatus.status)) {
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
      if (isActiveStatus(nextStatus.status)) {
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

  const runMaintenanceAction = (action: QuickAction) => {
    if (action.confirmMessage && !window.confirm(action.confirmMessage)) {
      return;
    }
    void startCliRun(action.mode);
  };

  useImperativeHandle(ref, () => ({
    startCollection: () => {
      void startCliRun("run-once");
    },
  }));

  useEffect(() => {
    if (!status) return;
    if (logTailRef.current) {
      logTailRef.current.scrollTop = logTailRef.current.scrollHeight;
    }
  }, [status?.log_tail]);

  useEffect(() => {
    if (!isRunActive) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isRunActive]);

  const visibleLogTail = (currentStatus: RunStatus | null): string => {
    if (!currentStatus || !currentStatus.log_tail) {
      return isActiveStatus(currentStatus?.status)
        ? "Waiting for output..."
        : "Run completed without log output.";
    }
    return currentStatus.log_tail;
  };

  useEffect(() => {
    let cancelled = false;
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
    } else {
      void listRuns(5)
        .then((runs) => {
          if (cancelled) return;
          const activeRun = runs.find((run) => isActiveStatus(run.status));
          if (!activeRun) return;
          acceptStatus(activeRun);
          startPolling();
          void refreshStatus();
        })
        .catch(() => {
          // The regular run controls surface API errors when the operator starts or refreshes a run.
        });
    }
    return () => {
      cancelled = true;
      stopPolling();
    };
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

      <div className="collection-action">
        <div>
          <p className="label">Collect jobs</p>
          <p className="hint">Run the complete collection pipeline using the configured keywords and locations.</p>
        </div>
        <button
          type="button"
          className="primary"
          disabled={isActionDisabled}
          onClick={() => void startCliRun("run-once")}
        >
          {loadingMode === "run-once" ? "Starting…" : "Collect now"}
        </button>
      </div>

      <details className="quick-actions maintenance-actions">
        <summary>
          <span>
            <span className="label">Maintenance</span>
            <span className="hint">Infrequent cleanup, parsing, and repair tools.</span>
          </span>
        </summary>
        <div className="qa-grid">
          {maintenanceActions.map((action) => (
            <button
              key={action.mode}
              type="button"
              className="qa-card"
              disabled={isActionDisabled}
              onClick={() => runMaintenanceAction(action)}
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
      </details>

      {error && <div className="error">{error}</div>}

      {status && (() => {
        const progress = status.progress;
        const knownSourceProgress = sourceProgress(progress);
        const metrics = status.metrics || progress?.metrics;
        const activity = [...(progress?.events || [])].reverse();
        const title = isActiveStatus(status.status)
          ? status.mode === "run-once"
            ? "Collection in progress"
            : `${modeLabel(status.mode)} in progress`
          : status.status === "succeeded"
            ? "Run completed"
            : `Run ${status.status}`;

        return (
          <div className="status">
            <div className="status-row">
              <div>
                <p className="label">Current run</p>
                <div className="id-row">
                  <code>{status.run_id}</code>
                  <span className={`pill small mode ${modeClass(status.mode)}`}>{modeLabel(status.mode)}</span>
                </div>
              </div>
              <span className={`pill ${status.status}`}>{status.status}</span>
            </div>
            <div className="run-progress">
              <div className="run-progress-copy" role="status" aria-live="polite">
                <p className="label">{progress?.stage || (isActiveStatus(status.status) ? "Working" : "Result")}</p>
                <strong>{title}</strong>
                <span>{progress?.label || fallbackProgressLabel(status)}</span>
                {isActiveStatus(status.status) && <span className="muted tiny">{formatElapsed(status.started_at, now)}</span>}
                {progress?.current_source && <span className="current-source">{progress.current_source}</span>}
              </div>
              {knownSourceProgress && (
                <div className="source-progress">
                  <div>
                    <span className="label">Source queries</span>
                    <strong>Sources {knownSourceProgress.completed} of {knownSourceProgress.total}</strong>
                  </div>
                  <progress value={knownSourceProgress.completed} max={knownSourceProgress.total}>
                    {knownSourceProgress.completed} of {knownSourceProgress.total}
                  </progress>
                </div>
              )}
              {metrics && (
                <div className="run-metrics" aria-label="Collection results">
                  {metricItems(metrics).map(([value, label]) => (
                    <span className="run-metric" key={label}><strong>{value}</strong>{label}</span>
                  ))}
                </div>
              )}
              {activity.length > 0 && (
                <div className="run-activity" aria-label="Recent collection activity">
                  <p className="label">Recent activity</p>
                  <ul>
                    {activity.map((entry, index) => (
                      <li key={`${entry.at}-${index}`} className={`activity-${entry.level}`}>
                        <time dateTime={entry.at}>{formatActivityTime(entry.at)}</time>
                        <span>{entry.message}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            {status.trigger === "scheduled" && <p className="muted tiny">Started by automatic collection.</p>}
            <details className="technical-log">
              <summary>
                <span>
                  <span className="label">Technical log</span>
                  <span className="hint">Raw terminal output for troubleshooting.</span>
                </span>
              </summary>
              <pre ref={logTailRef}>{visibleLogTail(status)}</pre>
            </details>
          </div>
        );
      })()}
    </section>
  );
});

RunPane.displayName = "RunPane";

export default RunPane;
