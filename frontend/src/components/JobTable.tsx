import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from "react";
import { fetchJobs } from "../api";
import type { Job } from "../types";

export interface JobTableHandle {
  reload: (reset?: boolean) => void;
}

interface JobTableProps {
  sources: string[];
  companies: string[];
  onSelect: (job: Job | null) => void;
}

type SortOption = "scraped_at_desc" | "scraped_at_asc" | "title_asc" | "title_desc";

const PAGE_SIZE = 5;

const JobTable = forwardRef<JobTableHandle, JobTableProps>(({ companies, onSelect }, ref) => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [company, setCompany] = useState("");
  const [sort, setSort] = useState<SortOption>("scraped_at_desc");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const pageNumber = useMemo(() => Math.floor(offset / PAGE_SIZE) + 1, [offset]);
  const hasNext = useMemo(() => offset + PAGE_SIZE < total, [offset, total]);
  const companyOptions = useMemo(() => companies || [], [companies]);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchJobs({
          limit: PAGE_SIZE,
          offset,
          company,
          sort,
        });
        if (cancelled) return;

        setJobs(data.items);
        setTotal(data.total);

        if (!data.items.length) {
          setSelectedId(null);
          onSelect(null);
          return;
        }

        const existing = data.items.find((item) => item.job_id === selectedId);
        if (existing) {
          setSelectedId(existing.job_id);
          onSelect(existing);
        } else {
          setSelectedId(data.items[0].job_id);
          onSelect(data.items[0]);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load jobs.");
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
  }, [offset, company, sort, reloadToken]);

  useImperativeHandle(ref, () => ({
    reload: (reset = true) => {
      if (reset) {
        setOffset(0);
      }
      setReloadToken((token) => token + 1);
    },
  }));

  const selectJob = (job: Job | null) => {
    setSelectedId(job?.job_id || null);
    onSelect(job);
  };

  const nextPage = () => {
    if (!hasNext) return;
    setOffset((value) => value + PAGE_SIZE);
  };

  const prevPage = () => {
    setOffset((value) => Math.max(0, value - PAGE_SIZE));
  };

  const formatDate = (value?: string) => {
    if (!value) return "n/a";
    return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
  };

  const clearFilters = () => {
    setCompany("");
    setSort("scraped_at_desc");
    setOffset(0);
    setReloadToken((token) => token + 1);
  };

  return (
    <section className="panel job-table">
      <div className="table-header">
        <div>
          <p className="label">Jobs</p>
          <h3>{total ? total.toLocaleString() : "No"} records</h3>
        </div>
      </div>

      <div className="filters inline">
        <label className="tiny">
          <span>Company</span>
          <select
            value={company}
            className="input"
            onChange={(e) => {
              setCompany(e.target.value);
              setOffset(0);
              setReloadToken((token) => token + 1);
            }}
          >
            <option value="">Any company</option>
            {companyOptions.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="tiny">
          <span>Sort</span>
          <select
            value={sort}
            className="input"
            onChange={(e) => {
              setSort(e.target.value as SortOption);
              setOffset(0);
              setReloadToken((token) => token + 1);
            }}
          >
            <option value="scraped_at_desc">Newest</option>
            <option value="scraped_at_asc">Oldest</option>
            <option value="title_asc">Title A-Z</option>
            <option value="title_desc">Title Z-A</option>
          </select>
        </label>
        <button className="ghost sm" type="button" onClick={clearFilters} disabled={loading}>
          Clear
        </button>
      </div>

      <div className="table-card">
        {error ? (
          <div className="error">
            {error}
            <button className="ghost sm" type="button" onClick={() => setReloadToken((token) => token + 1)} disabled={loading}>
              Retry
            </button>
          </div>
        ) : (
          <>
            {loading ? (
              <div className="skeletons">
                {Array.from({ length: 5 }).map((_, idx) => (
                  <div key={idx} className="skeleton-row" />
                ))}
              </div>
            ) : jobs.length === 0 ? (
              <div className="empty">No jobs found. Try loosening filters.</div>
            ) : (
              <div className="list">
                {jobs.map((job) => (
                  <article
                    key={job.job_id}
                    className={`row ${job.job_id === selectedId ? "active" : ""}`}
                    onClick={() => selectJob(job)}
                  >
                    <div className="title">{job.title}</div>
                    <div className="meta">
                      <span>{job.company}</span>
                      <span>•</span>
                      <span>{job.location}</span>
                    </div>
                    <div className="tags">
                      <span className="tag">{job.source}</span>
                      {job.salary ? <span className="tag soft">{job.salary}</span> : null}
                      <span className="tag soft">{formatDate(job.scraped_at)}</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <div className="pagination">
        <button className="ghost" type="button" disabled={offset === 0 || loading} onClick={prevPage}>
          Prev
        </button>
        <span className="label">Page {pageNumber}</span>
        <button className="ghost" type="button" disabled={!hasNext || loading} onClick={nextPage}>
          Next
        </button>
      </div>
    </section>
  );
});

JobTable.displayName = "JobTable";

export default JobTable;
