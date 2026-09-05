import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { getRunStatus, listRuns, startRun } from "../api";
import type { RunStatus } from "../types";

interface RunPaneProps {
  className?: string;
  onRunCompleted?: (run: RunStatus) => void;
  onRunStatusChange?: (run: RunStatus | null) => void;
}

export interface RunPaneHandle {
  startCollection: () => void;
}

const ACTIVE_RUN_STORAGE_KEY = "job-informer.active-run";

const isActiveStatus = (status?: RunStatus["status"]): boolean =>
  status === "starting" || status === "running";

const formatElapsed = (startedAt: string, now: number): string => {
  const started = Date.parse(startedAt);
  if (Number.isNaN(started)) return "just started";
  const totalSeconds = Math.max(0, Math.floor((now - started) / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
};

const queryProgress = (status: RunStatus | null): { completed: number; total: number } | null => {
  const progress = status?.progress;
  if (!progress || progress.completed_queries == null || progress.total_queries == null || progress.total_queries <= 0) {
    return null;
  }
  return {
    completed: Math.min(Math.max(0, progress.completed_queries), progress.total_queries),
    total: progress.total_queries,
  };
};

const runSummary = (status: RunStatus | null): string => {
  if (!status) return "Manual collection using the configured searches.";
  if (isActiveStatus(status.status)) {
    return status.progress?.label || (status.status === "starting" ? "Preparing collection…" : "Collecting jobs…");
  }
  if (status.status === "succeeded") {
    const metrics = status.metrics || status.progress?.metrics;
    if (metrics) {
      return `${metrics.observed ?? 0} observed · ${metrics.new ?? 0} new · ${metrics.archived ?? 0} filtered`;
    }
    return "Collection completed.";
  }
  return status.status === "failed" ? "Collection failed." : `Collection ${status.status}.`;
};

const requestFailureLabel = (count: number): string =>
  `${count} request ${count === 1 ? "failure" : "failures"}`;

const RunPane = forwardRef<RunPaneHandle, RunPaneProps>(
  ({ className, onRunCompleted, onRunStatusChange }, ref) => {
    const [status, setStatus] = useState<RunStatus | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [now, setNow] = useState(() => Date.now());
    const pollHandle = useRef<number | null>(null);
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

    const isRunActive = isActiveStatus(status?.status);
    const isActionDisabled = loading || isRunActive;
    const progress = queryProgress(status);

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
        // Collection still works when browser storage is unavailable.
      }
    };

    const forgetActiveRun = (runId: string) => {
      try {
        if (window.sessionStorage.getItem(ACTIVE_RUN_STORAGE_KEY) === runId) {
          window.sessionStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
        }
      } catch {
        // Terminal-state handling does not depend on browser storage.
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
        const message = err instanceof Error ? err.message : "Failed to load collection status.";
        if (streak >= 5) {
          setError(`${message} Live updates paused; reload the page to reconnect.`);
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

    const startCollection = async () => {
      if (isActionDisabled) return;

      pollErrorStreak.current = 0;
      setError(null);
      setLoading(true);
      try {
        const nextStatus = await startRun({ mode: "run-once" });
        acceptStatus(nextStatus);
        if (isActiveStatus(nextStatus.status)) {
          startPolling();
          await refreshStatus();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to start collection.");
      } finally {
        setLoading(false);
      }
    };

    useImperativeHandle(ref, () => ({
      startCollection: () => {
        void startCollection();
      },
    }));

    useEffect(() => {
      if (!isRunActive) return;
      setNow(Date.now());
      const timer = window.setInterval(() => setNow(Date.now()), 1000);
      return () => window.clearInterval(timer);
    }, [isRunActive]);

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
            if (activeRun) {
              acceptStatus(activeRun);
              startPolling();
              void refreshStatus();
              return;
            }
            const latestRun = runs[0];
            if (latestRun) {
              runIdRef.current = latestRun.run_id;
              setStatus(latestRun);
            }
          })
          .catch(() => {
            // Starting a run will surface connection errors directly.
          });
      }
      return () => {
        cancelled = true;
        stopPolling();
      };
    }, []);

    const wrapperClassName = ["processing-row", "run-pane", className].filter(Boolean).join(" ");
    const statusText = status?.status === "succeeded" ? null : status?.status;

    return (
      <section className={wrapperClassName} aria-label="LinkedIn collection">
        <div className="processing-header">
          <div className="processing-copy">
            <h3>LinkedIn collection {statusText && <span className={`pill small ${status?.status}`}>{statusText}</span>}</h3>
            <p className="collection-summary" role="status" aria-live="polite">{runSummary(status)}</p>
            {isRunActive && (
              <p className="current-source">
                {status?.progress?.current_query || "Starting the first search"} · {formatElapsed(status!.started_at, now)}
              </p>
            )}
          </div>
          <button
            type="button"
            className="ghost sm"
            disabled={isActionDisabled}
            onClick={() => void startCollection()}
          >
            {loading ? "Starting…" : isRunActive ? "Collecting…" : "Collect jobs"}
          </button>
        </div>

        {isRunActive && progress && (
          <div className="collection-progress">
            <progress
              aria-label="LinkedIn collection progress"
              value={progress.completed}
              max={progress.total}
            >
              {progress.completed} of {progress.total}
            </progress>
            <span>{progress.completed}/{progress.total} searches</span>
          </div>
        )}

        {status?.scope_comparison && (
          <details className="processing-details">
          <summary>Last collection details</summary>
          <div className="scope-comparison" aria-label="Country-wide and city search comparison">
            <div className="scope-comparison-heading">
              <p className="label">Country-wide vs retained cities</p>
              <span className="muted small">
                {status.scope_comparison.shared_jobs} shared · {status.scope_comparison.city_jobs_covered_by_countrywide_percent}% of city jobs covered country-wide
              </span>
            </div>
            <div className="scope-comparison-grid">
              <div>
                <span className="scope-name">
                  {status.scope_comparison.countrywide.locations.join(" + ")}
                </span>
                <strong>{status.scope_comparison.countrywide.unique_jobs} unique jobs</strong>
                <span className="muted small">
                  {status.scope_comparison.countrywide.pages_completed}/{status.scope_comparison.countrywide.pages_attempted} pages · {requestFailureLabel(status.scope_comparison.countrywide.request_failures)}
                </span>
              </div>
              <div>
                <span className="scope-name">
                  {status.scope_comparison.cities.locations.join(" + ")}
                </span>
                <strong>{status.scope_comparison.cities.unique_jobs} unique jobs</strong>
                <span className="muted small">
                  {status.scope_comparison.cities.pages_completed}/{status.scope_comparison.cities.pages_attempted} pages · {requestFailureLabel(status.scope_comparison.cities.request_failures)}
                </span>
              </div>
            </div>
            <p className="scope-difference muted small">
              {status.scope_comparison.countrywide_only_jobs} country-only · {status.scope_comparison.city_only_jobs} city-only
            </p>
          </div>
          </details>
        )}

        {error && <div className="error" role="alert">{error}</div>}
      </section>
    );
  }
);

RunPane.displayName = "RunPane";

export default RunPane;
