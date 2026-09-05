import React, { useEffect, useRef, useState } from "react";
import { fetchDescription, reparseDescription, requestDescription } from "../api";
import type { JobDescriptionResponse } from "../types";

const date = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

interface Props { jobId: string; refreshToken?: number; onQueueChanged?: () => void }

const JobDescription: React.FC<Props> = ({ jobId, refreshToken = 0, onQueueChanged }) => {
  const [result, setResult] = useState<JobDescriptionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const mounted = useRef(true);
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
  const latest = result?.attempts[0];
  const pending = latest?.status === "queued" || latest?.status === "fetching";
  return <section className="job-description" aria-labelledby="job-description-title">
    <div className="description-heading"><h3 id="job-description-title">Original description</h3>
      <button type="button" className="ghost sm" disabled={busy || loading || pending} onClick={() => void act()}>
        {busy ? "Working…" : latest?.status === "fetching" ? "Fetching…" : latest?.status === "queued" ? "Queued" : saved ? "Refresh source" : latest?.error_kind ? "Retry description" : "Fetch description"}
      </button>
    </div>
    {loading && <p className="muted small">Loading saved description…</p>}
    {error && <div className="description-error" role="alert"><p>{error}</p><button type="button" className="ghost sm" onClick={() => setReloadToken((token) => token + 1)}>Reload saved description</button></div>}
    {pending && <p className="description-notice">{latest?.status === "fetching" ? "Retrieving the public LinkedIn posting…" : result?.queue.paused ? "Saved in the queue. Use Resume descriptions above to continue." : "Queued for retrieval. You can keep reviewing other jobs."}</p>}
    {latest?.error_message && !pending && <p className="description-notice">{latest.error_message}{saved ? " Showing the last saved description." : ""}</p>}
    {!loading && !saved && !pending && <p className="muted small">Fetch the public posting to save its full description and listed job criteria for later analysis.</p>}
    {saved && <>
      <p className="muted small">Saved {date(saved.fetched_at)} · LinkedIn{latest?.status === "unchanged" ? " · Source unchanged" : ""}</p>
      {saved.data.criteria.length > 0 && <dl className="description-criteria" aria-label="Listed job criteria">{saved.data.criteria.map((item, index) => <div key={`${item.key}-${index}`}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>}
      <div className="description-copy">{saved.data.description_text}</div>
    </>}
    {result && (result.source_versions > 0 || result.attempts.length > 0) && <details className="description-history">
      <summary>Saved source & retrieval history</summary>
      <p>{result.source_versions} saved source {result.source_versions === 1 ? "version" : "versions"}. Original source content is retained for future extraction.</p>
      {saved && <p>Parser {saved.extractor_version} · Schema {saved.schema_version} · Parsed {date(saved.extracted_at)}</p>}
      {result.reparse_available && <button type="button" className="ghost sm" disabled={busy || pending} onClick={() => void act(true)}>Reparse saved source</button>}
      <ol>{result.attempts.map((attempt) => <li key={attempt.fetch_id}><strong>{attempt.status.replace(/_/g, " ")}</strong> · {date(attempt.finished_at || attempt.requested_at)}{attempt.http_status ? ` · HTTP ${attempt.http_status}` : ""}{attempt.error_message && <span>{attempt.error_message}</span>}</li>)}</ol>
    </details>}
  </section>;
};

export default JobDescription;
