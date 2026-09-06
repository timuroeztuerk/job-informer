import React, { useEffect, useRef, useState } from "react";
import { controlAIQueue, fetchAIQueue, queueAI } from "../api";
import type { AIQueueState } from "../types";

const ExtractionQueue: React.FC<{ onChanged: () => void; onActivityChange?: (message: string) => void }> = ({ onChanged, onActivityChange }) => {
  const [state, setState] = useState<AIQueueState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const lastCompleted = useRef<number | null>(null);
  useEffect(() => {
    const pending = (state?.counts.running || 0) + (state?.counts.queued || 0) + (state?.counts.retry_wait || 0);
    const failed = (state?.counts.failed || 0) + (state?.counts.interrupted || 0);
    onActivityChange?.(error ? "AI status unavailable" : state?.paused ? "AI paused" : pending ? `${pending} AI outputs pending` : failed ? `${failed} AI outputs need attention` : "");
  }, [state, error, onActivityChange]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const next = await fetchAIQueue();
        if (cancelled) return;
        setState(next);
        setError(null);
        const completed = next.counts.succeeded || 0;
        if (lastCompleted.current !== null && completed !== lastCompleted.current) onChanged();
        lastCompleted.current = completed;
        if (next.counts.running || (!next.paused && (next.counts.queued || next.counts.retry_wait))) timer = window.setTimeout(load, 2000);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load AI extraction.");
      }
    };
    void load();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [reload, onChanged]);

  const act = async (action: "start" | "retry" | "pause" | "resume") => {
    setBusy(true);
    setError(null);
    try {
      setState(action === "start" || action === "retry" ? await queueAI(action === "retry") : await controlAIQueue(action));
      setReload((value) => value + 1);
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update AI extraction.");
    } finally { setBusy(false); }
  };
  const counts = state?.counts || {};
  const queued = (counts.queued || 0) + (counts.retry_wait || 0);
  const failed = (counts.failed || 0) + (counts.interrupted || 0);
  const primaryAction = state?.paused ? "resume" : counts.running || queued ? "pause" : "start";
  const startLabel = state ? `Extract next ${state.batch_size}` : "Extract jobs";
  return <section className="processing-row" aria-label="AI extraction">
    <div className="processing-header">
      <div className="processing-copy">
        <h3>AI extraction</h3>
        <p>{state ? [
          `${(counts.succeeded || 0).toLocaleString()} ready`, counts.running && `${counts.running} processing`,
          queued && `${queued} queued`, failed && `${failed} need attention`,
        ].filter(Boolean).join(" · ") : "Loading extraction status…"}</p>
      </div>
      <button className="ghost sm" type="button" disabled={busy || !state || (primaryAction !== "pause" && !state.configured)} onClick={() => void act(primaryAction)}
        aria-label={primaryAction === "start" ? startLabel : primaryAction === "pause" ? "Pause AI extraction" : "Resume AI extraction"}>
        {busy ? "Working…" : primaryAction === "start" ? startLabel : primaryAction === "pause" ? "Pause" : "Resume"}
      </button>
    </div>
    {state && !state.configured && <p className="description-notice">Configure the backend OpenAI API key to start extraction.</p>}
    {state && (state.paused || state.cooldown_until > Date.now()/1000) && <p className="description-notice">{state.reason || (state.paused ? "AI extraction is paused." : "AI extraction is waiting.")}{state.cooldown_until > Date.now()/1000 ? ` Waiting until ${new Date(state.cooldown_until*1000).toLocaleTimeString()}.` : ""}</p>}
    {error && <p role="alert" className="description-error">{error} <button type="button" className="ghost sm" onClick={() => setReload((value) => value+1)}>Reload AI status</button></p>}
    <details className="processing-details">
      <summary>Results & options</summary>
      {state && <p>Each click queues up to {state.batch_size} active jobs with saved descriptions, with active favorites first. Filtered and archived jobs are excluded from extraction and retries. Current results and queued jobs are skipped; click again for the next batch.</p>}
      <div className="processing-options">
        {primaryAction !== "start" && <button className="ghost sm" type="button" disabled={busy || !state?.configured} onClick={() => void act("start")}>{startLabel}</button>}
        {failed > 0 && <button className="ghost sm" type="button" disabled={busy || !state?.configured} onClick={() => void act("retry")}>Retry next {state?.batch_size} failed</button>}
      </div>
      {Boolean(state?.jobs.length) && <ul className="processing-jobs" aria-label="Recent AI jobs">
        {state!.jobs.map((job) => <li key={job.job_id}><a href={`/?view=dashboard&job=${encodeURIComponent(job.job_id)}`}>{job.title}</a><span>{job.company} · {job.status?.replace(/_/g, " ") || "ready to queue"}</span></li>)}
      </ul>}
      {state && state.usage.input_tokens > 0 && <div className="processing-usage">
        <dl>
          <div><dt>Input tokens</dt><dd>{state.usage.input_tokens.toLocaleString()}</dd></div>
          <div><dt>Output tokens</dt><dd>{state.usage.output_tokens.toLocaleString()}</dd></div>
          <div><dt>Reasoning tokens</dt><dd>{state.usage.reasoning_tokens.toLocaleString()}</dd></div>
          <div><dt>Estimated cost</dt><dd>${state.estimated_cost_usd.toFixed(4)}</dd></div>
        </dl>
        <p>Reasoning is included in output tokens. Totals cover responses with recorded usage.</p>
      </div>}
    </details>
  </section>;
};

export default ExtractionQueue;
