import React, { useEffect, useMemo, useState } from "react";
import { deleteJob as archiveJob, fetchJob, updateJobAnnotation } from "../api";
import type { Job, JobAnnotation, JobAnnotationPriority, JobAnnotationStatus, ParsedPayload } from "../types";

interface JobDetailProps {
  job: Job | null;
  onDeleted: () => void;
  onAnnotationSaved: () => void;
}

const defaultAnnotation: JobAnnotation = {
  status: "unreviewed",
  priority: "medium",
  notes: "",
  why_interesting: "",
  skill_gaps: [],
  follow_up_date: null,
  resume_version: "",
  created_at: null,
  updated_at: null,
};

const annotationStatuses: JobAnnotationStatus[] = [
  "unreviewed",
  "interesting",
  "applied",
  "interviewing",
  "offer",
  "rejected",
  "archived",
];
const annotationPriorities: JobAnnotationPriority[] = ["high", "medium", "low"];

const normalizeAnnotation = (annotation?: JobAnnotation | null): JobAnnotation => ({
  ...defaultAnnotation,
  ...(annotation || {}),
  skill_gaps: annotation?.skill_gaps || [],
});

const JobDetail: React.FC<JobDetailProps> = ({ job, onDeleted, onAnnotationSaved }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [details, setDetails] = useState<Job | null>(null);
  const [parsed, setParsed] = useState<ParsedPayload | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [savingAnnotation, setSavingAnnotation] = useState(false);
  const [annotation, setAnnotation] = useState<JobAnnotation>(defaultAnnotation);
  const [skillGapInput, setSkillGapInput] = useState("");

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      if (!job) {
        setLoading(false);
        setError(null);
        setDetails(null);
        setParsed(null);
        setAnnotation(defaultAnnotation);
        setSkillGapInput("");
        return;
      }
      setLoading(true);
      setError(null);
      setDetails(null);
      setParsed(null);
      setAnnotation(defaultAnnotation);
      setSkillGapInput("");
      try {
        const fullJob = await fetchJob(job.job_id);
        if (cancelled) return;
        setDetails(fullJob);
        setParsed((fullJob.parsed_description?.payload as ParsedPayload) || null);
        const nextAnnotation = normalizeAnnotation(fullJob.annotation);
        setAnnotation(nextAnnotation);
        setSkillGapInput(nextAnnotation.skill_gaps.join(", "));
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load job.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [job?.job_id]);

  const formatDate = (value?: string | null) => {
    if (!value) return "n/a";
    return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
  };

  const formatDateTime = (value?: string | null) => {
    if (!value) return "Not saved yet";
    return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  };

  const formatLabel = (value?: string | null) => {
    if (!value) return "unspecified";
    return value.replace(/_/g, " ");
  };

  const salaryText = (range?: { min?: number | null; max?: number | null }) => {
    if (!range) return "unspecified";
    const { min, max } = range;
    if (min && max) return `${min.toLocaleString()} – ${max.toLocaleString()}`;
    if (min) return `${min.toLocaleString()}+`;
    if (max) return `up to ${max.toLocaleString()}`;
    return "unspecified";
  };

  const benefitsText = (benefits?: string | string[]) => {
    if (!benefits) return "unspecified";
    if (Array.isArray(benefits)) return benefits.length ? benefits.join(", ") : "unspecified";
    return benefits;
  };

  const annotationUpdatedAt = useMemo(() => formatDateTime(annotation.updated_at), [annotation.updated_at]);
  const currentDetails = job && details?.job_id === job.job_id ? details : null;

  const confirmArchive = async () => {
    if (!job || !currentDetails || loading || archiving) return;
    const targetJobId = job.job_id;
    const ok = window.confirm("Archive this job? It will be hidden from the active job list.");
    if (!ok) return;
    setArchiving(true);
    try {
      await archiveJob(targetJobId);
      setDetails(null);
      setParsed(null);
      setAnnotation(defaultAnnotation);
      setSkillGapInput("");
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to archive job.");
    } finally {
      setArchiving(false);
    }
  };

  const saveAnnotation = async () => {
    if (!job || !currentDetails || loading || savingAnnotation) return;
    const targetJobId = job.job_id;
    setSavingAnnotation(true);
    setError(null);
    try {
      const saved = await updateJobAnnotation(targetJobId, {
        ...annotation,
        notes: annotation.notes.trim(),
        why_interesting: annotation.why_interesting.trim(),
        skill_gaps: skillGapInput
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        follow_up_date: annotation.follow_up_date || null,
        resume_version: annotation.resume_version.trim(),
      });
      setAnnotation(normalizeAnnotation(saved));
      setSkillGapInput((saved.skill_gaps || []).join(", "));
      setDetails((current) => (current ? { ...current, annotation: saved } : current));
      onAnnotationSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save annotation.");
    } finally {
      setSavingAnnotation(false);
    }
  };

  return (
    <div className="panel detail-pane">
      <p className="label">Details</p>
      {!job && <div>Select a job to see details.</div>}

      {job && (
        <div className="card">
          <header>
            <div>
              <p className="company">{job.company}</p>
              <h3>{job.title}</h3>
              <p className="muted">
                {job.location} • {job.source}
              </p>
            </div>
            <div className="header-actions">
              {job.url && (
                <a className="link" href={job.url} target="_blank" rel="noreferrer">
                  Open posting
                </a>
              )}
              <button
                className="danger"
                type="button"
                onClick={confirmArchive}
                disabled={archiving || loading || !currentDetails}
              >
                {archiving ? "Archiving…" : "Archive"}
              </button>
            </div>
          </header>

          {error && <div className="error">{error}</div>}
          {!error && loading && <div className="muted">Loading details…</div>}
          {!error && !loading && currentDetails && (
            <>
              <p className="muted">Scraped {formatDate(currentDetails.scraped_at)}</p>

              <details className="annotation-card">
                <summary className="annotation-header annotation-toggle">
                  <div>
                    <p className="summary-title">Personal notes</p>
                    <p className="muted small">Track your own pipeline, fit, and follow-up plan for this job.</p>
                  </div>
                  <div className="annotation-summary-meta">
                    <span className="muted tiny">Last saved: {annotationUpdatedAt}</span>
                    <span className="muted tiny">
                      {formatLabel(annotation.status)} · {formatLabel(annotation.priority)}
                    </span>
                  </div>
                </summary>

                <div className="annotation-grid">
                  <label>
                    <span>Status</span>
                    <select
                      value={annotation.status}
                      onChange={(e) =>
                        setAnnotation((current) => ({
                          ...current,
                          status: e.target.value as JobAnnotationStatus,
                        }))
                      }
                    >
                      {annotationStatuses.map((status) => (
                        <option key={status} value={status}>
                          {formatLabel(status)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Priority</span>
                    <select
                      value={annotation.priority}
                      onChange={(e) =>
                        setAnnotation((current) => ({
                          ...current,
                          priority: e.target.value as JobAnnotationPriority,
                        }))
                      }
                    >
                      {annotationPriorities.map((priority) => (
                        <option key={priority} value={priority}>
                          {formatLabel(priority)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Follow up</span>
                    <input
                      type="date"
                      value={annotation.follow_up_date || ""}
                      onChange={(e) =>
                        setAnnotation((current) => ({
                          ...current,
                          follow_up_date: e.target.value || null,
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>Resume version</span>
                    <input
                      type="text"
                      placeholder="e.g. ai-general-v2"
                      value={annotation.resume_version}
                      onChange={(e) =>
                        setAnnotation((current) => ({
                          ...current,
                          resume_version: e.target.value,
                        }))
                      }
                    />
                  </label>
                </div>

                <label className="annotation-field">
                  <span>Why interesting</span>
                  <textarea
                    rows={3}
                    placeholder="Why this role is worth your attention."
                    value={annotation.why_interesting}
                    onChange={(e) =>
                      setAnnotation((current) => ({
                        ...current,
                        why_interesting: e.target.value,
                      }))
                    }
                  />
                </label>

                <label className="annotation-field">
                  <span>Skill gaps</span>
                  <input
                    type="text"
                    placeholder="rag, deployment, experimentation"
                    value={skillGapInput}
                    onChange={(e) => setSkillGapInput(e.target.value)}
                  />
                  <span className="muted tiny">Comma-separated. Use this to surface repeated gaps across jobs.</span>
                </label>

                <label className="annotation-field">
                  <span>Notes</span>
                  <textarea
                    rows={5}
                    placeholder="Application angle, interview prep ideas, companies to revisit, etc."
                    value={annotation.notes}
                    onChange={(e) =>
                      setAnnotation((current) => ({
                        ...current,
                        notes: e.target.value,
                      }))
                    }
                  />
                </label>

                <div className="annotation-actions">
                  <button className="primary" type="button" onClick={saveAnnotation} disabled={savingAnnotation}>
                    {savingAnnotation ? "Saving…" : "Save notes"}
                  </button>
                  <span className="muted small">
                    Status: {formatLabel(annotation.status)} · Priority: {formatLabel(annotation.priority)}
                  </span>
                </div>
              </details>

              {parsed && (
                <details className="parsed parsed-card">
                  <summary className="parsed-toggle">
                    <div>
                      <p className="summary-title">Parsed signals</p>
                      <p className="muted small">Model-extracted metadata, skills, and compensation hints.</p>
                    </div>
                    <div className="parsed-summary-pills">
                      <span className="pill small">{parsed.seniority || "unspecified"}</span>
                      <span className="pill small">{parsed.remote || "unspecified"}</span>
                    </div>
                  </summary>

                  <div className="pill-row">
                    <span className="pill">{parsed.seniority || "unspecified"}</span>
                    <span className="pill">{parsed.employment_type || "unspecified"}</span>
                    <span className="pill">{parsed.remote || "unspecified"}</span>
                    <span className="pill">{parsed.location?.[0] || job.location}</span>
                  </div>

                  <div className="summary">
                    <p className="summary-title">Summary</p>
                    {parsed.summary ? <p className="body">{parsed.summary}</p> : <p className="muted">No parsed summary yet.</p>}
                  </div>

                  <div className="grid">
                    <div>
                      <p className="summary-title">Languages</p>
                      <div className="chip-row">
                        {(parsed.languages || []).map((lang) => (
                          <span key={lang} className="chip">
                            {lang}
                          </span>
                        ))}
                        {(!parsed.languages || !parsed.languages.length) && <span className="muted small">n/a</span>}
                      </div>
                    </div>
                    <div>
                      <p className="summary-title">Programming</p>
                      <div className="chip-row">
                        {(parsed.programming_languages || []).map((lang) => (
                          <span key={lang} className="chip">
                            {lang}
                          </span>
                        ))}
                        {(!parsed.programming_languages || !parsed.programming_languages.length) && (
                          <span className="muted small">n/a</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="summary-title">Tools</p>
                      <div className="chip-row">
                        {(parsed.tools || []).map((tool) => (
                          <span key={tool} className="chip">
                            {tool}
                          </span>
                        ))}
                        {(!parsed.tools || !parsed.tools.length) && <span className="muted small">n/a</span>}
                      </div>
                    </div>
                    <div>
                      <p className="summary-title">Skills</p>
                      <div className="chip-row">
                        {(parsed.skills || []).map((skill) => (
                          <span key={skill} className="chip">
                            {skill}
                          </span>
                        ))}
                        {(!parsed.skills || !parsed.skills.length) && <span className="muted small">n/a</span>}
                      </div>
                    </div>
                  </div>

                  <div className="meta">
                    <div>
                      <p className="summary-title">Degree</p>
                      <p className="muted">
                        {parsed.degree_field || "unspecified"} • {parsed.degree_type || "unspecified"}
                      </p>
                    </div>
                    <div>
                      <p className="summary-title">Experience</p>
                      <p className="muted">{parsed.years_experience_min ?? "unspecified"}+ years</p>
                    </div>
                    <div>
                      <p className="summary-title">Salary (EUR)</p>
                      <p className="muted">{salaryText(parsed.salary_eur_range)}</p>
                    </div>
                    <div>
                      <p className="summary-title">Benefits</p>
                      <p className="muted">{benefitsText(parsed["extra benefits"])}</p>
                    </div>
                  </div>
                </details>
              )}

              {currentDetails.description ? (
                <details className="raw">
                  <summary>Full description</summary>
                  <p className="body">{currentDetails.description}</p>
                </details>
              ) : (
                <p className="muted">No description stored.</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default JobDetail;
