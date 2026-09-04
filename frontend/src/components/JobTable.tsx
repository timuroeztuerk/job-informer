import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { fetchJob, fetchJobs } from "../api";
import type { Job, JobArchiveFilter, QueryGroupOption } from "../types";
import { readEnumParam, readPositiveIntegerParam, readTextParam, replaceSearchParams } from "../urlState";

export interface JobTableHandle {
  reload: (reset?: boolean) => void;
}

interface JobTableProps {
  companies: string[];
  roleFamilies?: string[];
  queryGroups?: QueryGroupOption[];
  onSelect: (job: Job | null) => void;
  refreshToken?: number;
}

type SortOption =
  | "scraped_at_desc"
  | "scraped_at_asc"
  | "last_seen_desc"
  | "last_seen_asc"
  | "title_asc"
  | "title_desc";

const PAGE_SIZE = 5;
const archiveFilterOptions: JobArchiveFilter[] = ["exclude", "only", "include"];
const sortOptions: SortOption[] = [
  "scraped_at_desc",
  "scraped_at_asc",
  "last_seen_desc",
  "last_seen_asc",
  "title_asc",
  "title_desc",
];

const JobTable = forwardRef<JobTableHandle, JobTableProps>(
  ({ companies, roleFamilies = [], queryGroups = [], onSelect, refreshToken = 0 }, ref) => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(() => (readPositiveIntegerParam("page", 1) - 1) * PAGE_SIZE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [search, setSearch] = useState(() => readTextParam("search"));
  const [location, setLocation] = useState(() => readTextParam("location"));
  const [company, setCompany] = useState(() => readTextParam("company"));
  const [roleFamily, setRoleFamily] = useState(() => readTextParam("role"));
  const [queryGroup, setQueryGroup] = useState(() => readTextParam("query_group"));
  const [archiveFilter, setArchiveFilter] = useState<JobArchiveFilter>(
    () => readEnumParam("archived", archiveFilterOptions) || "exclude"
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
        readTextParam("company") ||
        readTextParam("role") ||
        readTextParam("query_group") ||
        readTextParam("archived") ||
        readTextParam("from") ||
        readTextParam("to")
      )
  );

  const pageNumber = useMemo(() => Math.floor(offset / PAGE_SIZE) + 1, [offset]);
  const hasNext = useMemo(() => offset + PAGE_SIZE < total, [offset, total]);
  const companyOptions = useMemo(() => companies || [], [companies]);
  const roleFamilyOptions = useMemo(() => roleFamilies || [], [roleFamilies]);
  const queryGroupOptions = useMemo(() => queryGroups || [], [queryGroups]);
  const activeFilterCount = useMemo(
    () =>
      [
        search.trim(),
        location.trim(),
        company,
        roleFamily,
        queryGroup,
        archiveFilter === "exclude" ? "" : archiveFilter,
        dateFrom,
        dateTo,
      ].filter(Boolean).length,
    [archiveFilter, company, dateFrom, dateTo, location, queryGroup, roleFamily, search]
  );
  const querySignature = JSON.stringify([
    offset,
    search,
    location,
    company,
    roleFamily,
    queryGroup,
    archiveFilter,
    dateFrom,
    dateTo,
    sort,
  ]);

  useEffect(() => {
    replaceSearchParams({
      search,
      location,
      source: "",
      company,
      role: roleFamily,
      query_group: queryGroup,
      archived: archiveFilter === "exclude" ? "" : archiveFilter,
      status: "",
      priority: "",
      from: dateFrom,
      to: dateTo,
      sort: sort === "scraped_at_desc" ? "" : sort,
      page: pageNumber > 1 ? String(pageNumber) : "",
      job: selectedId || "",
    });
  }, [
    archiveFilter,
    company,
    dateFrom,
    dateTo,
    location,
    queryGroup,
    roleFamily,
    pageNumber,
    search,
    selectedId,
    sort,
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
          company,
          roleFamily,
          queryGroup,
          archived: archiveFilter,
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
    setCompany("");
    setRoleFamily("");
    setQueryGroup("");
    setArchiveFilter("exclude");
    setDateFrom("");
    setDateTo("");
    setOffset(0);
    setReloadToken((token) => token + 1);
  };

  const formatLabel = (value?: string | null) => {
    if (!value) return "";
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
              {activeFilterCount ? `${activeFilterCount} filter${activeFilterCount === 1 ? "" : "s"} applied` : "Search and narrow jobs"}
            </p>
          </div>
          {activeFilterCount > 0 && <span className="filter-menu-badge">{activeFilterCount} active</span>}
          <span className="filter-menu-action">{filtersOpen ? "Close" : "Open filters"}</span>
        </summary>
        <div className="filter-panel">
          <div className="filter-grid">
          <label className="filter-field filter-field-wide">
            <span>Search</span>
            <input
              type="search"
              value={search}
              className="input"
              placeholder="Search title, company, or location"
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
          </label>
          <label className="filter-field">
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
          <label className="filter-field">
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
          <label className="filter-field">
            <span>Company</span>
            <input
              type="search"
              list="job-company-options"
              value={company}
              className="input"
              placeholder="Type a company name"
              onChange={(e) => {
                setCompany(e.target.value);
                setOffset(0);
              }}
            />
            <datalist id="job-company-options">
              {companyOptions.map((item) => <option key={item} value={item} />)}
            </datalist>
          </label>
          <label className="filter-field">
            <span>Role family</span>
            <select
              value={roleFamily}
              className="input"
              onChange={(e) => {
                setRoleFamily(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Any role family</option>
              {roleFamilyOptions.map((item) => (
                <option key={item} value={item}>{formatLabel(item)}</option>
              ))}
            </select>
          </label>
          <label className="filter-field">
            <span>Found via</span>
            <select
              value={queryGroup}
              className="input"
              onChange={(e) => {
                setQueryGroup(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">Any query group</option>
              {queryGroupOptions.map((item) => (
                <option key={item.key} value={item.key}>{item.name}</option>
              ))}
            </select>
          </label>
          <fieldset className="filter-date-range filter-field-wide">
            <legend>First seen</legend>
            <label className="filter-field">
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
            <label className="filter-field">
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
          </fieldset>
          </div>
          <div className="filter-actions">
            <span className="muted tiny">Results update automatically.</span>
            <div>
              <button className="ghost sm" type="button" onClick={clearFilters} disabled={loading || activeFilterCount === 0}>
                Clear filters
              </button>
              <button className="filter-done" type="button" onClick={() => setFiltersOpen(false)}>
                Done
              </button>
            </div>
          </div>
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
                {jobs.map((job) => {
                  const repeatCount = Math.max(0, (job.seen_count ?? 1) - 1);
                  const firstSeenAt = job.first_seen_at || job.scraped_at;
                  const latestSeenAt = job.last_seen_at || job.scraped_at;
                  return (
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
                        {job.role_family ? (
                          <span className="tag soft">{formatLabel(job.role_family)}</span>
                        ) : null}
                        {job.archived_at ? (
                          <span className="tag soft">Archived job · {formatDate(job.archived_at)}</span>
                        ) : null}
                        {job.archived_reason ? <span className="tag soft">Reason: {job.archived_reason}</span> : null}
                        {repeatCount > 0 ? (
                          <span className="tag soft recurrence-note">
                            Repeated {repeatCount} {repeatCount === 1 ? "time" : "times"}
                          </span>
                        ) : null}
                        {repeatCount > 0 && latestSeenAt ? (
                          <span className="tag soft">Latest repeat {formatDate(latestSeenAt)}</span>
                        ) : null}
                        {job.salary ? <span className="tag soft">{job.salary}</span> : null}
                        {firstSeenAt ? <span className="tag soft">First seen {formatDate(firstSeenAt)}</span> : null}
                      </div>
                    </article>
                  );
                })}
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
