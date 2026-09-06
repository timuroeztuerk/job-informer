import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { fetchDescription, reparseDescription, requestDescription } from "../api";
import type { JobDescriptionResponse } from "../types";

const date = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

interface Props {
  jobId: string;
  archived?: boolean;
  refreshToken?: number;
  onQueueChanged?: () => void;
  actionTarget?: HTMLElement | null;
  criteriaTarget?: HTMLElement | null;
  history?: React.ReactNode;
  children?: React.ReactNode;
  view?: string;
}

const JobDescription: React.FC<Props> = ({ jobId, archived = false, refreshToken = 0, onQueueChanged, actionTarget, criteriaTarget, history, children, view = "description" }) => {
  const [result, setResult] = useState<JobDescriptionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const mounted = useRef(true);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = 0; }, [jobId, view]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const value = await fetchDescription(jobId);
        if (cancelled) return;
        setResult(value);
        setError(null);
        const status = value.attempts[0]?.status;
        if (status === "fetching" || (status === "queued" && !value.queue.paused)) timer = window.setTimeout(load, 1500);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load the saved description.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [jobId, refreshToken, reloadToken]);

  const act = async (reparse = false) => {
    if (archived && !reparse) return;
    setBusy(true);
    setError(null);
    try {
      const value = reparse ? await reparseDescription(jobId) : await requestDescription(jobId, Boolean(result?.saved));
      onQueueChanged?.();
      if (!mounted.current) return;
      setResult(value);
      setReloadToken((token) => token + 1);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "Could not retrieve the description.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const saved = result?.saved;
  const earlierCollection = saved?.source_kind === "legacy_text";
  const latest = result?.attempts[0];
  const pending = latest?.status === "queued" || latest?.status === "fetching";
  const sourceAction = <button type="button" className="ghost sm source-button" disabled={archived || busy || loading || pending}
    title={archived ? "Restore this job to fetch its description." : undefined} onClick={() => void act()}>
    {busy ? "Working…" : latest?.status === "fetching" ? "Fetching…" : latest?.status === "queued" ? "Queued" : saved ? "Refresh source" : latest?.error_kind ? "Retry description" : "Fetch description"}
  </button>;
  const criteria = saved && saved.data.criteria.length > 0 && <dl className="description-criteria" aria-label="Listed job criteria">
    {saved.data.criteria.map((item, index) => <div key={`${item.key}-${index}`} title={`${item.label}: ${item.value}`}>
      <dt className="sr-only">{item.label}</dt><dd>{item.value}</dd>
    </div>)}
  </dl>;
  return <section className="job-description" aria-label={view === "description" ? "Original description" : "AI insights and source history"}>
    {actionTarget === undefined ? sourceAction : actionTarget && createPortal(sourceAction, actionTarget)}
    {criteriaTarget === undefined ? criteria : criteriaTarget && createPortal(criteria, criteriaTarget)}
    {loading && <p className="muted small">Loading saved description…</p>}
    {error && <div className="description-error" role="alert"><p>{error}</p><button type="button" className="ghost sm" onClick={() => setReloadToken((token) => token + 1)}>Reload saved description</button></div>}
    {pending && (!archived || latest?.status === "fetching") && <p className="description-notice">{latest?.status === "fetching" ? "Retrieving the public LinkedIn posting…" : result?.queue.paused ? "Saved in the queue. Open Collection & processing to resume descriptions." : "Queued for retrieval. You can keep reviewing other jobs."}</p>}
    {latest?.error_message && !pending && <p className="description-notice">{latest.error_message}{saved ? " Showing the last saved description." : ""}</p>}
    {archived && <p className="muted small">Description fetching is limited to active jobs. Restore this job to fetch its description.</p>}
    {!archived && !loading && !saved && !pending && <p className="muted small">Fetch the public posting to save its full description and listed job criteria for later analysis.</p>}
    <div className="description-scroll" ref={scrollRef} role="region" aria-label={view === "description" ? "Description text and source history" : "AI insights and history"} tabIndex={0}>
    {children ?? (saved && <div className="description-copy">{saved.data.description_text}</div>)}
    {(history || (result && (result.source_versions > 0 || result.attempts.length > 0))) && <details className="description-history">
      <summary>Job & source history</summary>
      {history}
      {result && <p>{result.source_versions} saved source {result.source_versions === 1 ? "version" : "versions"}. Original source content is retained for future extraction.</p>}
      {saved && <p>{earlierCollection ? `Earlier saved text connected on ${date(saved.extracted_at)}.` : `Parser ${saved.extractor_version} · Schema ${saved.schema_version} · Parsed ${date(saved.extracted_at)}`}</p>}
      {result?.reparse_available && <button type="button" className="ghost sm" disabled={busy || pending} onClick={() => void act(true)}>Reparse saved source</button>}
      <ol>{result?.attempts.map((attempt) => <li key={attempt.fetch_id}><strong>{attempt.status.replace(/_/g, " ")}</strong> · {date(attempt.finished_at || attempt.requested_at)}{attempt.http_status ? ` · HTTP ${attempt.http_status}` : ""}{attempt.error_message && <span>{attempt.error_message}</span>}</li>)}</ol>
    </details>}
    {saved && <p className="description-saved">Original description · {earlierCollection ? "Saved during an earlier collection" : "Saved"} · {date(saved.fetched_at)}{latest?.status === "unchanged" && latest.http_status ? " · Source unchanged" : ""}</p>}
    </div>
  </section>;
};

export default JobDescription;
