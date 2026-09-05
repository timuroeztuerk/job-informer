import React, { useEffect, useRef, useState } from "react";
import { archiveJob, requestJobExtraction, restoreJob, setJobFavorite, setJobFlag } from "../api";
import type { Job, JobFlag } from "../types";
import JobDescription from "./JobDescription";
import JobExtraction from "./JobExtraction";
import { jobLocation } from "../jobPresentation";

interface JobDetailProps {
  job: Job | null;
  onArchiveChanged: (clearSelection?: boolean) => void;
  onFavoriteChanged: (jobId: string, isFavorite: boolean) => void;
  onFlagChanged?: (flag: JobFlag) => void;
  onActionFeedback?: (message: string) => void;
  descriptionRefreshToken?: number;
  onDescriptionQueueChanged?: () => void;
}

const formatDate = (value?: string | null): string => {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
};

const JobDetail: React.FC<JobDetailProps> = ({ job, onArchiveChanged, onFavoriteChanged, onFlagChanged, onActionFeedback, descriptionRefreshToken, onDescriptionQueueChanged }) => {
  const [archiveAction, setArchiveAction] = useState<"archive" | "restore" | null>(null);
  const [favoriteAction, setFavoriteAction] = useState(false);
  const [flagAction, setFlagAction] = useState(false);
  const [aiAction, setAIAction] = useState(false);
  const [aiRefresh, setAIRefresh] = useState(0);
  const [flagReason, setFlagReason] = useState("");
  const [flagFeedback, setFlagFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("description");
  const [flagEditorOpen, setFlagEditorOpen] = useState(false);
  const [sourceActionTarget, setSourceActionTarget] = useState<HTMLDivElement | null>(null);
  const [criteriaTarget, setCriteriaTarget] = useState<HTMLDivElement | null>(null);
  const activeJobId = useRef("");

  useEffect(() => {
    activeJobId.current = job?.job_id || "";
    setArchiveAction(null);
    setFavoriteAction(false);
    setFlagAction(false);
    setAIAction(false);
    setAIRefresh(0);
    setFlagFeedback(null);
    setError(null);
    setFlagEditorOpen(false);
  }, [job?.job_id]);

  useEffect(() => {
    setFlagReason(job?.flag_reason || "");
  }, [job?.job_id, job?.flag_reason]);

  if (!job) {
    return (
      <aside className="panel detail-pane detail-empty">
        <p className="muted">Select a job to see its details.</p>
      </aside>
    );
  }

  const archived = Boolean(job.archived_at);
  const repeatCount = Math.max(0, (job.seen_count ?? 1) - 1);
  const repeatSummary = repeatCount === 0
    ? "Seen once"
    : `Repeated ${repeatCount} ${repeatCount === 1 ? "time" : "times"}`;
  const firstSeen = formatDate(job.first_seen_at || job.scraped_at);
  const lastSeen = formatDate(job.last_seen_at || job.scraped_at);
  const explanation = job.relevance_reason || job.archived_reason;

  const archive = async () => {
    if (archived || archiveAction) return;
    if (!window.confirm("Archive this job? It will be hidden from the active job list.")) return;
    const jobId = job.job_id;
    setArchiveAction("archive");
    setError(null);
    try {
      await archiveJob(jobId);
      onActionFeedback?.("Job archived.");
      onArchiveChanged(activeJobId.current === jobId);
    } catch (reason) {
      if (activeJobId.current === jobId) {
        setError(reason instanceof Error ? reason.message : "Failed to archive job.");
        setArchiveAction(null);
      }
    }
  };

  const restore = async () => {
    if (!archived || archiveAction) return;
    const jobId = job.job_id;
    setArchiveAction("restore");
    setError(null);
    try {
      await restoreJob(jobId);
      onActionFeedback?.("Job restored.");
      onArchiveChanged(activeJobId.current === jobId);
    } catch (reason) {
      if (activeJobId.current === jobId) {
        setError(reason instanceof Error ? reason.message : "Failed to restore job.");
        setArchiveAction(null);
      }
    }
  };

  const toggleFavorite = async () => {
    if (favoriteAction) return;
    const jobId = job.job_id;
    setFavoriteAction(true);
    setError(null);
    try {
      const result = await setJobFavorite(jobId, !job.is_favorite);
      if (activeJobId.current === jobId) {
        onFavoriteChanged(jobId, result.is_favorite);
        onActionFeedback?.(result.is_favorite ? "Job added to favorites." : "Job removed from favorites.");
        setFavoriteAction(false);
      }
    } catch (reason) {
      if (activeJobId.current === jobId) {
        setError(reason instanceof Error ? reason.message : "Failed to update favorite.");
        setFavoriteAction(false);
      }
    }
  };

  const saveFlag = async (isFlagged: boolean, reason?: string) => {
    if (flagAction) return;
    const jobId = job.job_id;
    setFlagAction(true);
    setFlagFeedback(null);
    setError(null);
    try {
      const result = await setJobFlag(jobId, isFlagged, reason);
      onFlagChanged?.(result);
      if (activeJobId.current === jobId) {
        setFlagAction(false);
        setFlagReason(result.flag_reason || "");
        if (isFlagged && reason === undefined) setFlagEditorOpen(true);
        setFlagFeedback(isFlagged ? reason === undefined ? "Flagged for filter review." : "Reason saved." : null);
        if (!isFlagged) onActionFeedback?.("Flag removed.");
      }
    } catch (reason) {
      if (activeJobId.current === jobId) {
        setError(reason instanceof Error ? reason.message : "Failed to update flag.");
        setFlagAction(false);
      }
    }
  };

  const extractAI = async () => {
    if (aiAction) return;
    const jobId = job.job_id;
    setAIAction(true);
    setError(null);
    setActiveTab("insights");
    try {
      const result = await requestJobExtraction(jobId);
      onDescriptionQueueChanged?.();
      if (activeJobId.current === jobId) {
        setAIRefresh((value) => value + 1);
        onActionFeedback?.(result.status === "succeeded" && !result.stale ? "The AI result is up to date."
          : result.queue.paused ? "AI queued. Resume extraction in Collection & processing when ready." : "AI extraction queued for this job.");
      }
    } catch (reason) {
      if (activeJobId.current === jobId) setError(reason instanceof Error ? reason.message : "Could not queue AI extraction.");
    } finally {
      if (activeJobId.current === jobId) setAIAction(false);
    }
  };

  return (
    <aside className="panel detail-pane">
      <div className="detail-header">
        <div className="detail-heading">
          <p className="company">{job.company}</p>
          <h2>{job.title}</h2>
          <div className="detail-meta" tabIndex={0} role="group" aria-label="Location and listed job criteria">
            <span className="detail-location">{jobLocation(job)}</span>
            <div className="detail-criteria" ref={setCriteriaTarget} />
          </div>
        </div>
        <div className="detail-actions">
          <button
            className={`flag-button ${job.is_flagged ? "is-flagged" : ""}`}
            type="button"
            aria-pressed={Boolean(job.is_flagged)}
            title={job.is_flagged ? "Remove this example from filter review" : "Flag as a job you are not looking for"}
            onClick={() => saveFlag(!job.is_flagged)}
            disabled={flagAction}
          >
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M5 21V3m0 1c5-4 9 4 14 0v10c-5 4-9-4-14 0" />
            </svg>
            {flagAction ? "Saving…" : job.is_flagged ? "Unflag" : "Flag"}
          </button>
          <button
            className={`favorite-button ${job.is_favorite ? "is-favorite" : ""}`}
            type="button"
            aria-pressed={job.is_favorite}
            aria-label={job.is_favorite ? "Remove from favorites" : "Add to favorites"}
            onClick={toggleFavorite}
            disabled={favoriteAction}
          >
            <span aria-hidden="true">{job.is_favorite ? "★" : "☆"}</span>
            {favoriteAction
              ? "Saving…"
              : job.is_favorite
                ? "Favorited"
                : "Favorite"}
          </button>
          {job.url && (
            <a className="link" href={job.url} target="_blank" rel="noreferrer">
              Open LinkedIn
            </a>
          )}
          {archived ? (
            <button className="primary" type="button" onClick={restore} disabled={Boolean(archiveAction)}>
              {archiveAction === "restore" ? "Restoring…" : "Restore"}
            </button>
          ) : (
            <button className="danger" type="button" onClick={archive} disabled={Boolean(archiveAction)}>
              {archiveAction === "archive" ? "Archiving…" : "Archive"}
            </button>
          )}
          <button className="ai-extract-button" type="button" disabled={aiAction} onClick={() => void extractAI()}
            title="Extract this job’s saved description. Current results are reused; failed attempts can be retried.">
            {aiAction ? "Queueing AI…" : "Extract AI"}
          </button>
          <div className="description-action" ref={setSourceActionTarget} />
        </div>
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      <div className="detail-tabs" role="tablist" aria-label="Job information">
        {[["description", "Description"], ["insights", "AI insights"]].map(([id, name], index) => (
          <button key={id} id={`job-tab-${id}`} type="button" role="tab" aria-selected={activeTab === id}
            aria-controls={`job-panel-${id}`} tabIndex={activeTab === id ? 0 : -1}
            onClick={() => setActiveTab(id)}
            onKeyDown={(event) => {
              const tabs = ["description", "insights"];
              const next = event.key === "ArrowRight" || event.key === "ArrowLeft" ? (index + 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
              if (next === null) return;
              event.preventDefault();
              setActiveTab(tabs[next]);
              document.getElementById(`job-tab-${tabs[next]}`)?.focus();
            }}>{name}</button>
        ))}
      </div>

      {job.is_flagged && (
        <details className="flag-note" open={flagEditorOpen} onToggle={(event) => setFlagEditorOpen(event.currentTarget.open)}>
          <summary>Flag reason{job.flag_reason ? ` · ${job.flag_reason}` : " · Add an optional note"}</summary>
          <form className="flag-review" onSubmit={(event) => { event.preventDefault(); void saveFlag(true, flagReason); }}>
            <label htmlFor="flag-reason">Why isn’t this a fit? <span className="muted">(optional)</span></label>
            <textarea id="flag-reason" className="input" rows={2} maxLength={2000} value={flagReason}
              placeholder="For example: mostly sales, or too senior."
              disabled={flagAction}
              onChange={(event) => { setFlagReason(event.target.value); setFlagFeedback(null); }} />
            <div className="flag-review-actions">
              <button className="ghost sm" type="submit" disabled={flagAction || flagReason.trim() === (job.flag_reason || "")}>
                Save reason
              </button>
              <span className="small" role="status">{flagFeedback || (flagReason.trim() !== (job.flag_reason || "") ? "Unsaved reason" : "")}</span>
            </div>
          </form>
        </details>
      )}

      <div key={job.job_id} role="tabpanel" id={`job-panel-${activeTab}`} aria-labelledby={`job-tab-${activeTab}`} tabIndex={0}>
        <JobDescription key={job.job_id} jobId={job.job_id} refreshToken={descriptionRefreshToken} onQueueChanged={onDescriptionQueueChanged}
          actionTarget={sourceActionTarget} criteriaTarget={criteriaTarget} view={activeTab} history={<div className="job-history">
          <h3>Posting history</h3>
          <p className="sighting-line">First seen {firstSeen} · Last seen {lastSeen} · {repeatSummary}</p>
          {job.role_family && <p className="muted small">Role family: {job.role_family.replace(/_/g, " ")}</p>}
          {explanation && <p className="muted small relevance-line">{explanation}</p>}
          {job.archived_at && <p className="muted small">Archived {formatDate(job.archived_at)}{job.archived_reason ? ` · ${job.archived_reason}` : ""}</p>}
        {(job.posting_count ?? 1) > 1 && (
          <details className="consolidated-postings">
            <summary>{job.posting_count} matching postings · All locations and links</summary>
            <p className="muted small">Shown as one job. Review actions apply to all matching postings.</p>
            <ul>
              {job.postings?.map((posting) => (
                <li key={posting.job_id}>
                  {posting.url ? <a href={posting.url} target="_blank" rel="noreferrer">{posting.location}</a> : posting.location}
                </li>
              ))}
            </ul>
          </details>
        )}

        </div>}>
          {activeTab === "insights" ? <JobExtraction key={`ai-${job.job_id}`} jobId={job.job_id} refreshToken={(descriptionRefreshToken ?? 0) + aiRefresh} /> : undefined}
        </JobDescription>
      </div>
    </aside>
  );
};

export default JobDetail;
