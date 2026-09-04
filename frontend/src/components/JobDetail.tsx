import React, { useEffect, useRef, useState } from "react";
import { archiveJob, restoreJob } from "../api";
import type { Job } from "../types";

interface JobDetailProps {
  job: Job | null;
  onArchiveChanged: (clearSelection?: boolean) => void;
  onActionFeedback?: (message: string) => void;
}

const formatDate = (value?: string | null): string => {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
};

const JobDetail: React.FC<JobDetailProps> = ({ job, onArchiveChanged, onActionFeedback }) => {
  const [archiveAction, setArchiveAction] = useState<"archive" | "restore" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const activeJobId = useRef("");

  useEffect(() => {
    activeJobId.current = job?.job_id || "";
    setArchiveAction(null);
    setError(null);
  }, [job?.job_id]);

  if (!job) {
    return (
      <aside className="panel detail-pane detail-empty">
        <p className="muted">Select a job to see its details.</p>
      </aside>
    );
  }

  const archived = Boolean(job.archived_at);
  const repeatCount = Math.max(0, (job.seen_count ?? 1) - 1);
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

  return (
    <aside className="panel detail-pane">
      <div className="detail-header">
        <div>
          <p className="company">{job.company}</p>
          <h2>{job.title}</h2>
          <p className="muted">{job.location} · LinkedIn</p>
        </div>
        <div className="detail-actions">
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
        </div>
      </div>

      {error && <div className="error" role="alert">{error}</div>}

      <p className="sighting-line">
        First seen {firstSeen} · Last seen {lastSeen} · Repeated {repeatCount} {repeatCount === 1 ? "time" : "times"}
      </p>

      {explanation && <p className="muted small relevance-line">{explanation}</p>}
    </aside>
  );
};

export default JobDetail;
