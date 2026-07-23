import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchJobs, getApiErrorMessage, type JobsQuery } from "../api";
import type { Job, JobAnnotationStatus } from "../types";

type FitBand = "high" | "medium" | "low" | "zero" | "unscored";

type ReviewJob = Job & {
  fit_score?: number | null;
  fit_band?: FitBand | null;
};

export interface ReviewHomeDashboardFilters {
  job?: string;
  annotationStatus?: JobAnnotationStatus;
  dateFrom?: string;
  sort?: JobsQuery["sort"];
}

export interface ReviewHomeProps {
  onCollect: () => void;
  onOpenDashboard: (filters?: ReviewHomeDashboardFilters) => void;
  refreshToken?: number;
  className?: string;
  now?: Date;
}

interface ReviewHomeData {
  unreviewed: ReviewJob[];
  active: ReviewJob[];
  recent: ReviewJob[];
  recurring: ReviewJob[];
}

const EMPTY_DATA: ReviewHomeData = {
  unreviewed: [],
  active: [],
  recent: [],
  recurring: [],
};

const CLOSED_STATUSES = new Set<JobAnnotationStatus>(["rejected", "archived"]);

function localDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function recentDateKey(now: Date): string {
  const start = new Date(now);
  start.setDate(start.getDate() - 6);
  return localDateKey(start);
}

function formatDate(value?: string | null): string {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(date);
}

function formatFitScore(job: ReviewJob): string {
  return typeof job.fit_score === "number" ? Math.round(job.fit_score).toString() : "—";
}

function JobButton({ job, onOpen, kind }: { job: ReviewJob; onOpen: () => void; kind: "fit" | "standard" }) {
  const seenCount = job.seen_count ?? 1;

  if (kind === "fit") {
    const fitBand = job.fit_band || "unscored";
    return (
      <button type="button" className="fit-job" onClick={onOpen}>
        <span className="fit-score" aria-label={job.fit_score == null ? "Fit score unavailable" : `Fit score ${Math.round(job.fit_score)}`}>
          {formatFitScore(job)}
        </span>
        <span className="fit-job-copy">
          <span className="name">{job.title}</span>
          <span className="muted tiny">{job.company} · {job.location}</span>
        </span>
        <span className={`fit-band ${fitBand}`}>{fitBand}</span>
      </button>
    );
  }

  return (
    <button type="button" className="recurring-item" onClick={onOpen}>
      <span className="recurring-title">{job.title}</span>
      <span className="muted tiny">{job.company} · {job.location}</span>
      <span className="recurring-meta">
        {seenCount > 1 && <span className="pill small">Seen {seenCount}×</span>}
        <span className="muted tiny">Last seen {formatDate(job.last_seen_at || job.scraped_at)}</span>
      </span>
    </button>
  );
}

const ReviewHome: React.FC<ReviewHomeProps> = ({
  onCollect,
  onOpenDashboard,
  refreshToken = 0,
  className,
  now,
}) => {
  const [data, setData] = useState<ReviewHomeData>(EMPTY_DATA);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const referenceNow = useMemo(() => now || new Date(), [now]);
  const dateFrom = useMemo(() => recentDateKey(referenceNow), [referenceNow]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const [unreviewed, active, recent, recurring] = await Promise.all([
        fetchJobs({
          limit: 5,
          archived: "exclude",
          annotationStatus: "unreviewed",
          sort: "fit_score_desc",
        }),
        fetchJobs({ limit: 200, archived: "exclude", sort: "last_seen_desc" }),
        fetchJobs({ limit: 5, archived: "exclude", dateFrom, sort: "last_seen_desc" }),
        fetchJobs({ limit: 5, archived: "exclude", sort: "seen_count_desc" }),
      ]);

      setData({
        unreviewed: unreviewed.items,
        active: active.items,
        recent: recent.items,
        recurring: recurring.items.filter((job) => (job.seen_count ?? 1) > 1),
      });
    } catch (err) {
      setError(getApiErrorMessage(err, "Could not load the review queue from the API."));
    } finally {
      setLoading(false);
    }
  }, [dateFrom]);

  useEffect(() => {
    void load();
  }, [load, refreshToken, reloadToken]);

  const followUps = useMemo(() => {
    const today = localDateKey(referenceNow);
    return data.active
      .filter((job) => {
        const followUpDate = job.annotation?.follow_up_date;
        const status = job.annotation?.status;
        return Boolean(followUpDate && followUpDate <= today && (!status || !CLOSED_STATUSES.has(status)));
      })
      .sort((left, right) => (left.annotation?.follow_up_date || "").localeCompare(right.annotation?.follow_up_date || ""))
      .slice(0, 5);
  }, [data.active, referenceNow]);

  const openJob = (job: ReviewJob) => onOpenDashboard({ job: job.job_id });
  const wrapperClass = ["summary-page", className].filter(Boolean).join(" ");
  const hasQueueData = data.unreviewed.length > 0 || data.active.length > 0 || data.recent.length > 0 || data.recurring.length > 0;

  return (
    <section className={wrapperClass} aria-labelledby="review-home-title">
      <section className="panel intro intelligence-hero">
        <div className="intro-row intelligence-hero-row">
          <div>
            <p className="label">Today</p>
            <h2 id="review-home-title">Review the jobs that need attention</h2>
            <p className="muted">Start with strong profile matches, then clear due follow-ups and fresh sightings.</p>
          </div>
          <div className="intro-actions">
            <button className="ghost" type="button" onClick={() => setReloadToken((token) => token + 1)} disabled={loading}>
              Refresh
            </button>
            <button className="review-home-collect" type="button" onClick={onCollect}>
              Collect now
            </button>
          </div>
        </div>
        {error && (
          <div className="error" role="alert">
            {error}
            <button className="ghost sm" type="button" onClick={() => setReloadToken((token) => token + 1)} disabled={loading}>
              Retry
            </button>
          </div>
        )}
      </section>

      {loading && !hasQueueData && !error ? (
        <section className="panel intelligence-section" aria-live="polite">
          <p className="muted">Loading review queue…</p>
        </section>
      ) : error && !hasQueueData ? null : (
        <>
          <section className="panel intelligence-section">
            <div className="section-head">
              <div>
                <p className="label">Review queue</p>
                <h3>Best-fit unreviewed jobs</h3>
                <p className="muted small">Ranked by the active fit profile; unscored jobs may appear after scored matches.</p>
              </div>
              <button
                className="ghost"
                type="button"
                onClick={() => onOpenDashboard({ annotationStatus: "unreviewed", sort: "fit_score_desc" })}
              >
                Review all
              </button>
            </div>
            {data.unreviewed.length ? (
              <div className="fit-job-list">
                {data.unreviewed.map((job) => <JobButton key={job.job_id} job={job} kind="fit" onOpen={() => openJob(job)} />)}
              </div>
            ) : (
              <p className="muted small">No unreviewed active jobs are waiting.</p>
            )}
          </section>

          <div className="intel-grid two-up">
            <section className="panel intelligence-section">
              <div className="section-head">
                <div>
                  <p className="label">Follow-ups</p>
                  <h3>Due or overdue</h3>
                </div>
                <span className="pill small">{followUps.length} due</span>
              </div>
              {followUps.length ? (
                <div className="recurring-list">
                  {followUps.map((job) => (
                    <button key={job.job_id} type="button" className="recurring-item" onClick={() => openJob(job)}>
                      <span className="recurring-title">{job.title}</span>
                      <span className="muted tiny">{job.company} · {job.location}</span>
                      <span className="recurring-meta">
                        <span className="due-chip overdue">Due {formatDate(job.annotation?.follow_up_date)}</span>
                        {job.annotation?.status && <span className="pill small">{job.annotation.status}</span>}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="muted small">No due follow-ups among the latest {data.active.length} active jobs checked.</p>
              )}
            </section>

            <section className="panel intelligence-section">
              <div className="section-head">
                <div>
                  <p className="label">Newly seen</p>
                  <h3>Collected in the last 7 days</h3>
                </div>
                <button className="ghost" type="button" onClick={() => onOpenDashboard({ dateFrom, sort: "last_seen_desc" })}>
                  View all
                </button>
              </div>
              {data.recent.length ? (
                <div className="recurring-list">
                  {data.recent.map((job) => <JobButton key={job.job_id} job={job} kind="standard" onOpen={() => openJob(job)} />)}
                </div>
              ) : (
                <p className="muted small">No active jobs were first collected in this window.</p>
              )}
            </section>
          </div>

          <section className="panel intelligence-section recurring-panel">
            <div className="section-head">
              <div>
                <p className="label">Repeat sightings</p>
                <h3>Jobs that keep appearing</h3>
              </div>
              <button className="ghost" type="button" onClick={() => onOpenDashboard({ sort: "seen_count_desc" })}>
                View all recurring
              </button>
            </div>
            {data.recurring.length ? (
              <div className="fit-job-list">
                {data.recurring.map((job) => <JobButton key={job.job_id} job={job} kind="standard" onOpen={() => openJob(job)} />)}
              </div>
            ) : (
              <p className="muted small">No repeat sightings yet.</p>
            )}
          </section>
        </>
      )}
    </section>
  );
};

export default ReviewHome;
