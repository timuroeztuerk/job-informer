import React, { useEffect, useRef, useState } from "react";
import { controlAIQueue, fetchAIQueue, queueAI } from "../api";
import type { AIQueueState } from "../types";

const ExtractionQueue: React.FC<{ onChanged: () => void }> = ({ onChanged }) => {
  const [state, setState] = useState<AIQueueState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const lastCompleted = useRef<number | null>(null);
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
  return <section className="panel description-queue" aria-label="AI extraction">
    <div className="description-queue-bar"><div><p className="label">Extract job information</p>
      <p className="muted small">{state ? `${counts.succeeded || 0} ready · ${counts.running || 0} processing · ${(counts.queued || 0) + (counts.retry_wait || 0)} queued · ${(counts.failed || 0) + (counts.interrupted || 0)} need attention` : "Loading extraction status…"}</p>
    </div><div className="description-queue-actions">
      <button className="ghost sm" type="button" disabled={busy || !state?.configured} onClick={() => void act("start")}>{state?.pilot_limit ? `Extract first ${state.pilot_limit} jobs` : "Extract saved jobs"}</button>
      {Boolean(counts.failed || counts.interrupted) && <button className="ghost sm" type="button" disabled={busy} onClick={() => void act("retry")}>Retry AI outputs</button>}
      {state?.paused ? <button className="ghost sm" type="button" disabled={busy} onClick={() => void act("resume")}>Resume AI extraction</button>
        : Boolean(counts.running || counts.queued || counts.retry_wait) && <button className="ghost sm" type="button" disabled={busy} onClick={() => void act("pause")}>Pause AI extraction</button>}
    </div></div>
    {state?.pilot_limit && <p className="muted small">Trial limited to the same {state.pilot_limit} jobs, including after a restart. Open the selected jobs below to inspect the results.</p>}
    {state && !state.configured && <p className="description-notice">Configure the backend OpenAI API key to start extraction.</p>}
    {state && (state.paused || state.cooldown_until > Date.now()/1000) && <p className="description-notice">{state.reason}{state.cooldown_until > Date.now()/1000 ? ` Waiting until ${new Date(state.cooldown_until*1000).toLocaleTimeString()}.` : ""}</p>}
    {Boolean(state?.jobs.length) && <details className="extraction-cohort"><summary>Selected jobs · {state!.jobs.length}</summary>
      <ul>{state!.jobs.map((job) => <li key={job.job_id}><a href={`/?view=dashboard&job=${encodeURIComponent(job.job_id)}`}>{job.title}</a><span className="muted small">{job.company} · {job.status?.replace(/_/g, " ") || "ready to queue"}</span></li>)}</ul>
    </details>}
    {state && state.usage.input_tokens > 0 && <p className="muted tiny">{state.usage.input_tokens.toLocaleString()} input tokens · {state.usage.output_tokens.toLocaleString()} output tokens, including {state.usage.reasoning_tokens.toLocaleString()} reasoning tokens · Estimated ${state.estimated_cost_usd.toFixed(4)} for responses with recorded usage</p>}
    {error && <p role="alert" className="description-error">{error} <button type="button" className="ghost sm" onClick={() => setReload((value) => value+1)}>Reload AI status</button></p>}
  </section>;
};

export default ExtractionQueue;
