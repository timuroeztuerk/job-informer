import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { fetchJob, fetchJobs } from "../api";
import type { Job, JobAnnotationPriority, JobAnnotationStatus, JobArchiveFilter } from "../types";
import { readEnumParam, readPositiveIntegerParam, readTextParam, replaceSearchParams } from "../urlState";

export interface JobTableHandle {
  reload: (reset?: boolean) => void;
}

interface JobTableProps {
  sources: string[];
  companies: string[];
  onSelect: (job: Job | null) => void;
  refreshToken?: number;
}

type SortOption =
  | "scraped_at_desc"
  | "scraped_at_asc"
  | "last_seen_desc"
  | "last_seen_asc"
  | "seen_count_desc"
  | "seen_count_asc"
  | "fit_score_desc"
  | "fit_score_asc"
  | "title_asc"
  | "title_desc";

const PAGE_SIZE = 5;
const annotationStatusOptions: Array<JobAnnotationStatus | ""> = [
  "",
  "unreviewed",
  "interesting",
  "applied",
  "interviewing",
  "offer",
  "rejected",
  "archived",
];
const annotationPriorityOptions: Array<JobAnnotationPriority | ""> = ["", "high", "medium", "low"];
const archiveFilterOptions: JobArchiveFilter[] = ["exclude", "only", "include"];
const sortOptions: SortOption[] = [
  "scraped_at_desc",
  "scraped_at_asc",
  "last_seen_desc",
  "last_seen_asc",
  "seen_count_desc",
  "seen_count_asc",
  "fit_score_desc",
  "fit_score_asc",
  "title_asc",
  "title_desc",
];

const JobTable = forwardRef<JobTableHandle, JobTableProps>(
  ({ sources, companies, onSelect, refreshToken = 0 }, ref) => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(() => (readPositiveIntegerParam("page", 1) - 1) * PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [search, setSearch] = useState(() => readTextParam("search"));
  const [location, setLocation] = useState(() => readTextParam("location"));
  const [source, setSource] = useState(() => readTextParam("source"));
  const [company, setCompany] = useState(() => readTextParam("company"));
  const [archiveFilter, setArchiveFilter] = useState<JobArchiveFilter>(
    () => readEnumParam("archived", archiveFilterOptions) || "exclude"
  );
  const [annotationStatus, setAnnotationStatus] = useState<JobAnnotationStatus | "">(
    () => readEnumParam("status", annotationStatusOptions)
  );
  const [annotationPriority, setAnnotationPriority] = useState<JobAnnotationPriority | "">(
    () => readEnumParam("priority", annotationPriorityOptions)
  );
  const [dateFrom, setDateFrom] = useState(() => readTextParam("from"));
  const [dateTo, setDateTo] = useState(() => readTextParam("to"));
  const [sort, setSort] = useState<SortOption>(() => readEnumParam("sort", sortOptions) || "scraped_at_desc");
  const [selectedId, setSelectedId] = useState<string | null>(() => readTextParam("job") || null);
  const [reloadToken, setReloadToken] = useState(0);
  const lastQuerySignatureRef = useRef<string | null>(null);
  const advanceIfMissingRef = useRef(false);
  const [filtersOpen, setFiltersOpen] = useState(
    () =>
      !!(
        readTextParam("search") ||
        readTextParam("location") ||
        readTextParam("source") ||
        readTextParam("company") ||
        readTextParam("archived") ||
        readTextParam("status") ||
        readTextParam("priority") ||
        readTextParam("from") ||
        readTextParam("to")
      )
  );

  const pageNumber = useMemo(() => Math.floor(offset / PAGE_SIZE) + 1, [offset]);
  const hasNext = useMemo(() => offset + PAGE_SIZE < total, [offset, total]);
  const sourceOptions = useMemo(() => sources || [], [sources]);
  const companyOptions = useMemo(() => companies || [], [companies]);
  const activeFilterCount = useMemo(
    () =>
      [
        search.trim(),
        location.trim(),
        source,
        company,
        archiveFilter === "exclude" ? "" : archiveFilter,
        annotationStatus,
        annotationPriority,
        dateFrom,
        dateTo,
      ].filter(Boolean).length,
    [annotationPriority, annotationStatus, archiveFilter, company, dateFrom, dateTo, location, search, source]
  );
  const querySignature = JSON.stringify([
    offset,
    search,
    location,
    source,
    company,
    archiveFilter,
    annotationStatus,
    annotationPriority,
    dateFrom,
    dateTo,
    sort,
  ]);

  useEffect(() => {
    replaceSearchParams({
      search,
      location,
      source,
      company,
      archived: archiveFilter === "exclude" ? "" : archiveFilter,
      status: annotationStatus,
      priority: annotationPriority,
      from: dateFrom,
      to: dateTo,
      sort: sort === "scraped_at_desc" ? "" : sort,
      page: pageNumber > 1 ? String(pageNumber) : "",
      job: selectedId || "",
    });
  }, [
    annotationPriority,
    annotationStatus,
    archiveFilter,
    company,
    dateFrom,
    dateTo,
    location,
    pageNumber,
    search,
    selectedId,
    sort,
    source,
  ]);

  useEffect(() => {
    let cancelled = false;
    const queryChanged =
      lastQuerySignatureRef.current !== null && lastQuerySignatureRef.current !== querySignature;
    lastQuerySignatureRef.current = querySignature;

    const load = async () => {
      setLoading(true);
      setError(null);
      setSelectionError(null);
      try {
        const data = await fetchJobs({
          limit: PAGE_SIZE,
          offset,
          search,
          location,
          source,
          company,
          archived: archiveFilter,
          annotationStatus,
          annotationPriority,
          dateFrom,
          dateTo,
          sort,
        });
        if (cancelled) return;

        setJobs(data.items);
        setTotal(data.total);
        const advanceIfMissing = advanceIfMissingRef.current;
        advanceIfMissingRef.current = false;

        const existing = data.items.find((item) => item.job_id === selectedId);
        if (existing) {
          setSelectedId(existing.job_id);
          onSelect(existing);
          return;
        }

        if (selectedId && !queryChanged && !advanceIfMissing) {
          try {
            const requestedJob = await fetchJob(selectedId);
            if (cancelled) return;
            onSelect(requestedJob);
          } catch (err) {
            if (cancelled) return;
            onSelect(null);
            const message = err instanceof Error ? err.message : "The requested job could not be loaded.";
            setSelectionError(`Could not open job ${selectedId}: ${message}`);
          }
          return;
        }

        if (data.items.length) {
          setSelectedId(data.items[0].job_id);
          onSelect(data.items[0]);
        } else {
          setSelectedId(null);
          onSelect(null);
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
  }, [
    querySignature,
    reloadToken,
    refreshToken,
  ]);

  useImperativeHandle(ref, () => ({
    reload: (reset = true) => {
      if (reset) {
        setOffset(0);
        setSelectedId(null);
        onSelect(null);
      } else {
        advanceIfMissingRef.current = true;
      }
      setReloadToken((token) => token + 1);
    },
  }));

  const selectJob = (job: Job | null) => {
    setSelectionError(null);
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
    setSearch("");
    setLocation("");
    setSource("");
    setCompany("");
    setArchiveFilter("exclude");
    setAnnotationStatus("");
    setAnnotationPriority("");
    setDateFrom("");
    setDateTo("");
    setSort("scraped_at_desc");
    setOffset(0);
    setReloadToken((token) => token + 1);
  };

  const formatAnnotationLabel = (value?: string | null) => {
    if (!value) return "";
    if (value === "archived") return "Set aside (annotation)";
    return value.replace(/_/g, " ");
  };

  const recordLabel =
    archiveFilter === "only" ? "archived records" : archiveFilter === "include" ? "all records" : "active records";

  const handleRowKeyDown = (event: React.KeyboardEvent<HTMLElement>, job: Job) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectJob(job);
    }
  };

  return (
    <section className="panel job-table">
      <div className="table-header">
        <div>
          <p className="label">Jobs</p>
          <h3>
            {total ? total.toLocaleString() : "No"} {recordLabel}
          </h3>
        </div>
        <label className="tiny sort-control">
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
            <option value="last_seen_desc">Last seen</option>
            <option value="last_seen_asc">Least recent</option>
            <option value="seen_count_desc">Most recurring</option>
            <option value="seen_count_asc">Least recurring</option>
            <option value="fit_score_desc">Best profile fit</option>
            <option value="fit_score_asc">Lowest profile fit</option>
            <option value="title_asc">Title A-Z</option>
            <option value="title_desc">Title Z-A</option>
          </select>
        </label>
      </div>

      <details
        className="filter-menu"
        open={filtersOpen}
        onToggle={(event) => setFiltersOpen((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary className="filter-menu-toggle">
          <div>
            <p className="label">Filters</p>
            <p className="muted tiny">
              {activeFilterCount ? `${activeFilterCount} active filter${activeFilterCount === 1 ? "" : "s"}` : "Search, narrow, and sort"}
            </p>
          </div>
          <span className="filter-menu-badge">{activeFilterCount || "All"}</span>
        </summary>
        <div className="filters inline">
          <label className="tiny">
            <span>Job set</span>
            <select
              value={archiveFilter}
              className="input"
              onChange={(e) => {
                setArchiveFilter(e.target.value as JobArchiveFilter);
                setOffset(0);
              }}
            >
              <option value="exclude">Active jobs</option>
              <option value="only">Archived jobs</option>
              <option value="include">All jobs</option>
            </select>
          </label>
          <label className="tiny">
            <span>Search</span>
            <input
              type="search"
              value={search}
              className="input"
              placeholder="Title, notes, keywords"
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          <label className="tiny">
            <span>Location</span>
            <input
              type="search"
              value={location}
              className="input"
              placeholder="Berlin, remote, Germany"
              onChange={(e) => {
                setLocation(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          <label className="tiny">
            <span>Source</span>
            <select
              value={source}
              className="input"
              onChange={(e) => {
                setSource(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Any source</option>
              {sourceOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
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
            <span>From</span>
            <input
              type="date"
              value={dateFrom}
              className="input"
              onChange={(e) => {
                setDateFrom(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          <label className="tiny">
            <span>To</span>
            <input
              type="date"
              value={dateTo}
              className="input"
              onChange={(e) => {
                setDateTo(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          <label className="tiny">
            <span>Review status</span>
            <select
              value={annotationStatus}
              className="input"
              onChange={(e) => {
                setAnnotationStatus(e.target.value as JobAnnotationStatus | "");
                setOffset(0);
                setReloadToken((token) => token + 1);
              }}
            >
              <option value="">Any review status</option>
              {annotationStatusOptions
                .filter((value) => value)
                .map((value) => (
                  <option key={value} value={value}>
                    {formatAnnotationLabel(value)}
                  </option>
                ))}
            </select>
          </label>
          <label className="tiny">
            <span>Priority</span>
            <select
              value={annotationPriority}
              className="input"
              onChange={(e) => {
                setAnnotationPriority(e.target.value as JobAnnotationPriority | "");
                setOffset(0);
                setReloadToken((token) => token + 1);
              }}
            >
              <option value="">Any priority</option>
              {annotationPriorityOptions
                .filter((value) => value)
                .map((value) => (
                  <option key={value} value={value}>
                    {formatAnnotationLabel(value)}
                  </option>
                ))}
            </select>
          </label>
          <button className="ghost sm" type="button" onClick={clearFilters} disabled={loading}>
            Clear
          </button>
        </div>
      </details>

      {selectionError && (
        <div className="error">
          <span>{selectionError}</span>
          <button className="ghost sm" type="button" onClick={() => selectJob(null)}>
            Clear requested job
          </button>
        </div>
      )}

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
              <div className="empty">
                {archiveFilter === "only" ? "No archived jobs found." : "No jobs found. Try loosening filters."}
              </div>
            ) : (
              <div className="list">
                {jobs.map((job) => (
                  <article
                    key={job.job_id}
                    className={`row ${job.job_id === selectedId ? "active" : ""}`}
                    role="button"
                    tabIndex={0}
                    aria-pressed={job.job_id === selectedId}
                    onClick={() => selectJob(job)}
                    onKeyDown={(event) => handleRowKeyDown(event, job)}
                  >
                    <div className="title">{job.title}</div>
                    <div className="meta">
                      <span>{job.company}</span>
                      <span>•</span>
                      <span>{job.location}</span>
                    </div>
                    <div className="tags">
                      <span className="tag">{job.source}</span>
                      {job.annotation?.status ? (
                        <span className={`tag soft annotation-status annotation-${job.annotation.status}`}>
                          {formatAnnotationLabel(job.annotation.status)}
                        </span>
                      ) : null}
                      {job.archived_at ? (
                        <span className="tag soft">Archived job · {formatDate(job.archived_at)}</span>
                      ) : null}
                      {job.archived_reason ? <span className="tag soft">Reason: {job.archived_reason}</span> : null}
                      {job.annotation?.priority ? (
                        <span className={`tag soft annotation-priority priority-${job.annotation.priority}`}>
                          {formatAnnotationLabel(job.annotation.priority)}
                        </span>
                      ) : null}
                      {job.seen_count && job.seen_count > 1 ? <span className="tag soft">Seen {job.seen_count}x</span> : null}
                      {job.last_seen_at ? <span className="tag soft">Last seen {formatDate(job.last_seen_at)}</span> : null}
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
  }
);

JobTable.displayName = "JobTable";

export default JobTable;
