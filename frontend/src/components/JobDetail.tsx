import React, { useEffect, useState } from "react";
import { deleteJob, fetchJob } from "../api";
import type { Job, ParsedPayload } from "../types";

interface JobDetailProps {
  job: Job | null;
  onDeleted: () => void;
}

const JobDetail: React.FC<JobDetailProps> = ({ job, onDeleted }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [details, setDetails] = useState<Job | null>(null);
  const [parsed, setParsed] = useState<ParsedPayload | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      if (!job) {
        setDetails(null);
        setParsed(null);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const fullJob = await fetchJob(job.job_id);
        if (cancelled) return;
        setDetails(fullJob);
        setParsed((fullJob.parsed_description?.payload as ParsedPayload) || null);
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

  const formatDate = (value?: string) => {
    if (!value) return "n/a";
    return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
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

  const confirmDelete = async () => {
    if (!details || deleting) return;
    const ok = window.confirm("Delete this job? This cannot be undone.");
    if (!ok) return;
    setDeleting(true);
    try {
      await deleteJob(details.job_id);
      setDetails(null);
      setParsed(null);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete job.");
    } finally {
      setDeleting(false);
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
              <button className="danger" type="button" onClick={confirmDelete} disabled={deleting}>
                Delete
              </button>
            </div>
          </header>

          {error && <div className="error">{error}</div>}
          {!error && loading && <div className="muted">Loading details…</div>}
          {!error && !loading && details && (
            <>
              <p className="muted">Scraped {formatDate(details.scraped_at)}</p>

              {parsed && (
                <div className="parsed">
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
                        {(!parsed.programming_languages || !parsed.programming_languages.length) && <span className="muted small">n/a</span>}
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
                </div>
              )}

              {details.description ? (
                <details className="raw">
                  <summary>Full description</summary>
                  <p className="body">{details.description}</p>
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
