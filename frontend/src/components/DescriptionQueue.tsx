import React, { useEffect, useState } from "react";
import { controlDescriptionQueue, fetchDescriptionQueue, queueDescriptions } from "../api";
import type { DescriptionQueueState } from "../types";

interface Props { refreshToken: number; onChanged: () => void; onActivityChange?: (message: string) => void }

const DescriptionQueue: React.FC<Props> = ({ refreshToken, onChanged, onActivityChange }) => {
  const [state, setState] = useState<DescriptionQueueState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    onActivityChange?.(error ? "Description status unavailable" : state?.paused ? "Descriptions paused" : state?.fetching || state?.queued
      ? `${(state?.queued || 0) + (state?.fetching || 0)} descriptions pending`
      : state?.failed_jobs ? `${state.failed_jobs} descriptions need attention` : "");
  }, [state, error, onActivityChange]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const value = await fetchDescriptionQueue();
        if (cancelled) return;
        setState(value);
        setError(null);
        if (value.fetching || (value.queued && !value.paused)) timer = window.setTimeout(load, 1500);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load description retrieval.");
      }
    };
    void load();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [refreshToken, reload]);
  const act = async (action: "all" | "retry" | "pause" | "resume") => {
    setBusy(true);
    setError(null);
    try {
      const value = action === "all" || action === "retry" ? await queueDescriptions(action) : await controlDescriptionQueue(action);
      setState(value);
      setFeedback(value.added == null ? null : value.added ? `${value.added} descriptions added to the queue.` : "No eligible jobs to add.");
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update description retrieval.");
    } finally { setBusy(false); }
  };
  const primaryAction = state?.paused ? "resume" : state?.queued || state?.fetching ? "pause" : "all";
  return <section className="processing-row" aria-label="Description retrieval">
    <div className="processing-header">
      <div className="processing-copy">
        <h3>Descriptions</h3>
        <p>{state ? [
          `${state.saved_jobs.toLocaleString()} saved`, state.queued && `${state.queued} queued`,
          state.fetching && "Fetching one", state.failed_jobs && `${state.failed_jobs} need attention`,
        ].filter(Boolean).join(" · ") : "Loading retrieval status…"}</p>
      </div>
      <button type="button" className="ghost sm" disabled={busy || !state} onClick={() => void act(primaryAction)}
        aria-label={primaryAction === "all" ? "Fetch all" : primaryAction === "pause" ? "Pause descriptions" : "Resume descriptions"}>
        {busy ? "Working…" : primaryAction === "all" ? "Fetch all" : primaryAction === "pause" ? "Pause" : "Resume"}
      </button>
    </div>
    {state?.paused && <p className="description-notice">{state.reason || "Retrieval is paused."}{state.cooldown_until ? ` Resume after ${new Date(state.cooldown_until).toLocaleString()}.` : ""}</p>}
    {feedback && <p className="small" role="status">{feedback}</p>}
    {error && <div className="description-error" role="alert">{error} <button type="button" className="ghost sm" onClick={() => setReload((value) => value + 1)}>Reload retrieval status</button></div>}
    <details className="processing-details">
      <summary>Retrieval options</summary>
      <p>Fetch all reuses saved descriptions. It queues favorites first, then active jobs without an earlier attempt.</p>
      <div className="processing-options">
        {primaryAction !== "all" && <button type="button" className="ghost sm" disabled={busy || !state} onClick={() => void act("all")}>Fetch all</button>}
        {Boolean(state?.failed_jobs) && <>
          <button type="button" className="ghost sm" disabled={busy} onClick={() => void act("retry")}>Retry failed</button>
          <span>Retry up to 10 failed descriptions.</span>
        </>}
      </div>
    </details>
  </section>;
};

export default DescriptionQueue;
