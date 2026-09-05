import React, { useEffect, useState } from "react";
import { controlDescriptionQueue, fetchDescriptionQueue, queueDescriptions } from "../api";
import type { DescriptionQueueState } from "../types";

interface Props { refreshToken: number; onChanged: () => void }

const DescriptionQueue: React.FC<Props> = ({ refreshToken, onChanged }) => {
  const [state, setState] = useState<DescriptionQueueState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
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
  return <section className="panel description-queue" aria-label="Description retrieval">
    <div className="description-queue-bar"><div><p className="label">Descriptions</p><p className="muted small">{state ? `${state.saved_jobs} saved · ${state.queued} queued${state.fetching ? " · Fetching one" : ""}${state.failed_jobs ? ` · ${state.failed_jobs} need attention` : ""}` : "Loading retrieval status…"}</p></div>
      <div className="description-queue-actions">
        <button type="button" className="ghost sm" disabled={busy || !state} onClick={() => void act("all")}>Fetch all</button>
        {Boolean(state?.failed_jobs) && <button type="button" className="ghost sm" disabled={busy} onClick={() => void act("retry")}>Retry failed</button>}
        {state?.paused ? <button type="button" className="ghost sm" disabled={busy} onClick={() => void act("resume")}>Resume descriptions</button>
          : Boolean(state?.queued || state?.fetching) && <button type="button" className="ghost sm" disabled={busy} onClick={() => void act("pause")}>Pause descriptions</button>}
      </div>
    </div>
    <p className="muted small">Fetch all queues favorites first, then active jobs without saved text or an earlier attempt. Earlier descriptions are reused. Retry failed adds up to 10 jobs.</p>
    {state?.paused && <p className="description-notice">{state.reason || "Retrieval is paused."}{state.cooldown_until ? ` Resume after ${new Date(state.cooldown_until).toLocaleString()}.` : ""}</p>}
    {feedback && <p className="small" role="status">{feedback}</p>}
    {error && <div className="description-error" role="alert">{error} <button type="button" className="ghost sm" onClick={() => setReload((value) => value + 1)}>Reload retrieval status</button></div>}
  </section>;
};

export default DescriptionQueue;
