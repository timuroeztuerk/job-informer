import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { fetchJob, fetchJobs, setJobFavorite } from "../api";
import { jobLocation } from "../jobPresentation";
import type { Job, JobArchiveFilter, JobRelevanceFilter, QueryGroupOption } from "../types";
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
  | "seen_count_desc"
  | "seen_count_asc"
  | "title_asc"
  | "title_desc";

const PAGE_SIZE = 10;
const archiveFilterOptions: JobArchiveFilter[] = ["exclude", "only", "include"];
const relevanceFilterOptions: { value: JobRelevanceFilter; label: string }[] = [
  { value: "unmatched", label: "Unmatched" },
  { value: "auto_archived", label: "Automatically archived" },
  { value: "target", label: "Target" },
  { value: "excluded", label: "Excluded" },
  { value: "unrelated", label: "Unrelated" },
  { value: "manual_keep", label: "Manually kept" },
  { value: "manual_archive", label: "Manually archived" },
];
const sortOptions: SortOption[] = [
  "scraped_at_desc",
  "scraped_at_asc",
  "last_seen_desc",
  "last_seen_asc",
  "seen_count_desc",
  "seen_count_asc",
  "title_asc",
  "title_desc",
];

const meaningfulSalary = (value?: string): string | null => {
  const salary = value?.trim();
  if (!salary || /^(?:not specified|not available|n\/?a|unknown)$/i.test(salary)) {
    return null;
  }
  return salary;
};

const JobTable = forwardRef<JobTableHandle, JobTableProps>(
  ({ companies, roleFamilies = [], queryGroups = [], onSelect, refreshToken = 0 }, ref) => {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(() => (readPositiveIntegerParam("page", 1) - 1) * PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [search, setSearch] = useState(() => readTextParam("search"));
  const [location, setLocation] = useState(() => readTextParam("location"));
  const [company, setCompany] = useState(() => readTextParam("company"));
  const [companyExact, setCompanyExact] = useState(() => readTextParam("company_exact") === "1");
  const [locationPrimary, setLocationPrimary] = useState(() => readTextParam("location_primary") === "1");
  const [lastSeenFrom, setLastSeenFrom] = useState(() => readTextParam("seen_since"));
  const [repeatedOnly, setRepeatedOnly] = useState(() => readTextParam("repeated") === "1");
  const [roleFamily, setRoleFamily] = useState(() => readTextParam("role"));
  const [queryGroup, setQueryGroup] = useState(() => readTextParam("query_group"));
  const [relevanceFilter, setRelevanceFilter] = useState<JobRelevanceFilter | "">(
    () => readEnumParam("relevance_outcome", relevanceFilterOptions.map(({ value }) => value))
  );
  const [favoritesOnly, setFavoritesOnly] = useState(() => readTextParam("favorite") === "1");
  const [flaggedOnly, setFlaggedOnly] = useState(() => readTextParam("flagged") === "1");
  const [archiveFilter, setArchiveFilter] = useState<JobArchiveFilter>(
    () => readEnumParam("archived", archiveFilterOptions) || "exclude"
  );
  const [dateFrom, setDateFrom] = useState(() => readTextParam("from"));
  const [dateTo, setDateTo] = useState(() => readTextParam("to"));
  const [sort, setSort] = useState<SortOption>(() => readEnumParam("sort", sortOptions) || "scraped_at_desc");
  const [selectedId, setSelectedId] = useState<string | null>(() => readTextParam("job") || null);
  const [favoriteActionId, setFavoriteActionId] = useState<string | null>(null);
  const [favoriteError, setFavoriteError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const lastQuerySignatureRef = useRef<string | null>(null);
  const advanceIfMissingRef = useRef(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  const pageNumber = useMemo(() => Math.floor(offset / PAGE_SIZE) + 1, [offset]);
  const hasNext = useMemo(() => offset + PAGE_SIZE < total, [offset, total]);
  const companyOptions = useMemo(() => companies || [], [companies]);
  const roleFamilyOptions = useMemo(() => roleFamilies || [], [roleFamilies]);
  const queryGroupOptions = useMemo(() => queryGroups || [], [queryGroups]);
  const effectiveArchiveFilter = relevanceFilter === "auto_archived" ? "only" : archiveFilter;
  const activeFilterCount = useMemo(
    () =>
      [
        search.trim(),
        location.trim(),
        company,
        roleFamily,
        queryGroup,
        relevanceFilter,
        favoritesOnly ? "favorite" : "",
        flaggedOnly ? "flagged" : "",
        relevanceFilter === "auto_archived" || archiveFilter === "exclude" ? "" : archiveFilter,
        dateFrom,
        dateTo,
        lastSeenFrom,
        repeatedOnly ? "repeated" : "",
      ].filter(Boolean).length,
    [archiveFilter, company, dateFrom, dateTo, favoritesOnly, flaggedOnly, lastSeenFrom, location, queryGroup, relevanceFilter, repeatedOnly, roleFamily, search]
  );
  const querySignature = JSON.stringify([
    offset,
    search,
    location,
    company,
    companyExact,
    locationPrimary,
    lastSeenFrom,
    repeatedOnly,
    roleFamily,
    queryGroup,
    relevanceFilter,
    favoritesOnly,
    flaggedOnly,
    archiveFilter,
    dateFrom,
    dateTo,
    sort,
  ]);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0;
  }, [querySignature]);

  useEffect(() => {
    replaceSearchParams({
      search,
      location,
      source: "",
      company,
      company_exact: companyExact && company ? "1" : "",
      location_primary: locationPrimary && location ? "1" : "",
      seen_since: lastSeenFrom,
      repeated: repeatedOnly ? "1" : "",
      role: roleFamily,
      query_group: queryGroup,
      relevance_outcome: relevanceFilter,
      favorite: favoritesOnly ? "1" : "",
      flagged: flaggedOnly ? "1" : "",
      archived: effectiveArchiveFilter === "exclude" ? "" : effectiveArchiveFilter,
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
    effectiveArchiveFilter,
    company,
    companyExact,
    locationPrimary,
    lastSeenFrom,
    repeatedOnly,
    dateFrom,
    dateTo,
    favoritesOnly,
    flaggedOnly,
    location,
    queryGroup,
    relevanceFilter,
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
          companyExact,
          locationPrimary,
          lastSeenFrom,
          repeated: repeatedOnly,
          roleFamily,
          queryGroup,
          relevanceOutcome: relevanceFilter || undefined,
          favorite: favoritesOnly,
          flagged: flaggedOnly,
          archived: effectiveArchiveFilter,
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
    setCompanyExact(false);
    setLocationPrimary(false);
    setLastSeenFrom("");
    setRepeatedOnly(false);
    setRoleFamily("");
    setQueryGroup("");
    setRelevanceFilter("");
    setFavoritesOnly(false);
    setFlaggedOnly(false);
    setArchiveFilter("exclude");
    setDateFrom("");
    setDateTo("");
    setOffset(0);
    setReloadToken((token) => token + 1);
  };

  const formatLabel = (value?: string | null) => {
    if (!value) return "";
    if (value === "unclassified") return "Other / unclassified";
    return value.replace(/_/g, " ");
  };

  const recordSetLabel =
    effectiveArchiveFilter === "only" ? "archived records" : effectiveArchiveFilter === "include" ? "all records" : "active records";
  const recordLabel = `${flaggedOnly ? "flagged " : ""}${favoritesOnly ? "favorite " : ""}${recordSetLabel}`;

  const toggleFavorite = async (job: Job) => {
    if (favoriteActionId) return;
    setFavoriteActionId(job.job_id);
    setFavoriteError(null);
    try {
      const result = await setJobFavorite(job.job_id, !job.is_favorite);
      const updatedJob = { ...job, is_favorite: result.is_favorite };
      if (selectedId === job.job_id) {
        onSelect(updatedJob);
      }
      if (favoritesOnly && !result.is_favorite) {
        advanceIfMissingRef.current = selectedId === job.job_id;
        setReloadToken((token) => token + 1);
      } else {
        setJobs((current) => current.map((item) => item.job_id === job.job_id ? updatedJob : item));
      }
    } catch (reason) {
      setFavoriteError(reason instanceof Error ? reason.message : "Failed to update favorite.");
    } finally {
      setFavoriteActionId(null);
    }
  };

  return (
    <section className="panel job-table">
      <div className="table-header">
        <div className="job-list-heading">
          <h3>Jobs</h3>
          <span className="job-count" role="status" title={recordLabel}
            aria-label={loading && jobs.length === 0 ? "Loading jobs…" : `${total.toLocaleString()} ${recordLabel}`}>
            {loading && jobs.length === 0 ? "…" : total.toLocaleString()}
          </span>
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
            <option value="seen_count_desc">Most repeated</option>
            <option value="seen_count_asc">Least repeated</option>
            <option value="title_asc">Title A-Z</option>
            <option value="title_desc">Title Z-A</option>
          </select>
        </label>
      </div>

      <label className="job-search">
        <span className="sr-only">Search</span>
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
      <div className="filter-menu">
        <div className="job-filter-toolbar">
          <button className="filter-menu-toggle" type="button" aria-expanded={filtersOpen}
            aria-controls="job-filter-panel" title="Company, location, role & more"
            onClick={() => setFiltersOpen((open) => !open)}>
            <span>Filters{activeFilterCount > 0 ? ` · ${activeFilterCount}` : ""}</span>
            <span aria-hidden="true">{filtersOpen ? "−" : "+"}</span>
          </button>
          <div className="job-quick-filters">
            <label className="favorite-filter">
              <input
                type="checkbox" aria-label="Favorites only"
                checked={favoritesOnly}
                onChange={(event) => {
                  setFavoritesOnly(event.target.checked);
                  setOffset(0);
                }}
              />
              <span>Favorites</span>
            </label>
            <label className="favorite-filter">
              <input type="checkbox" aria-label="Flagged only" checked={flaggedOnly} onChange={(event) => {
                setFlaggedOnly(event.target.checked);
                setOffset(0);
              }} />
              <span>Flagged</span>
            </label>
            {activeFilterCount > 0 && <button className="text-button" type="button" aria-label="Clear filters" onClick={clearFilters}>Clear</button>}
          </div>
        </div>
        <div className="filter-panel" id="job-filter-panel" hidden={!filtersOpen}>
          <div className="filter-grid">
          <label className="filter-field">
            <span>Job set</span>
            <select
              value={effectiveArchiveFilter}
              disabled={relevanceFilter === "auto_archived"}
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
            <span>Relevance</span>
            <select
              value={relevanceFilter}
              className="input"
              onChange={(event) => {
                const next = event.target.value as JobRelevanceFilter | "";
                if (relevanceFilter === "auto_archived") setArchiveFilter("exclude");
                setRelevanceFilter(next);
                setOffset(0);
              }}
            >
              <option value="">Any relevance outcome</option>
              {relevanceFilterOptions.map(({ value, label }) => (
                <option key={value} value={value}>{label}</option>
              ))}
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
                setLocationPrimary(false);
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
                setCompanyExact(false);
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
              <option value="unclassified">Other / unclassified</option>
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
                value={dateFrom.slice(0, 10)}
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
                value={dateTo.slice(0, 10)}
                className="input"
                onChange={(e) => {
                  setDateTo(e.target.value);
                  setOffset(0);
                }}
              />
            </label>
          </fieldset>
          <label className="filter-field">
            <span>Last seen since</span>
            <input
              type="date"
              value={lastSeenFrom.slice(0, 10)}
              className="input"
              onChange={(event) => { setLastSeenFrom(event.target.value); setOffset(0); }}
            />
          </label>
          <label className="favorite-filter">
            <input
              type="checkbox"
              checked={repeatedOnly}
              onChange={(event) => { setRepeatedOnly(event.target.checked); setOffset(0); }}
            />
            <span>Seen more than once</span>
          </label>
          </div>
          <div className="filter-actions">
            <span className="muted tiny">Results update automatically.</span>
            <div>
              <button className="filter-done" type="button" onClick={() => setFiltersOpen(false)}>
                Done
              </button>
            </div>
          </div>
        </div>
      </div>

      {Boolean(company || location || roleFamily || queryGroup || relevanceFilter || dateFrom || dateTo || lastSeenFrom || repeatedOnly || effectiveArchiveFilter !== "exclude") && (
        <p className="filter-scope muted small">{[
          company && `Company: ${company}`, location && `Location: ${location}`,
          roleFamily && `Role: ${formatLabel(roleFamily)}`,
          queryGroup && `Found via: ${queryGroupOptions.find((item) => item.key === queryGroup)?.name || queryGroup}`,
          relevanceFilter && formatLabel(relevanceFilter),
          effectiveArchiveFilter !== "exclude" && (effectiveArchiveFilter === "only" ? "Archived jobs" : "Including archived"),
          dateFrom && `From ${dateFrom.slice(0, 10)}`, dateTo && `To ${dateTo.slice(0, 10)}`,
          lastSeenFrom && `Seen since ${lastSeenFrom.slice(0, 10)}`, repeatedOnly && "Seen more than once",
        ].filter(Boolean).join(" · ")}</p>
      )}

      {selectionError && (
        <div className="error">
          <span>{selectionError}</span>
          <button className="ghost sm" type="button" onClick={() => selectJob(null)}>
            Clear requested job
          </button>
        </div>
      )}

      {favoriteError && <div className="error" role="alert">{favoriteError}</div>}

      <div className="table-card" ref={listRef}>
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
                {Array.from({ length: PAGE_SIZE }).map((_, idx) => (
                  <div key={idx} className="skeleton-row" />
                ))}
              </div>
            ) : jobs.length === 0 ? (
              <div className="empty">
                {flaggedOnly
                  ? "No flagged jobs match these filters. Flag jobs from the detail panel to collect examples."
                  : favoritesOnly
                  ? "No favorite jobs found."
                  : effectiveArchiveFilter === "only"
                    ? "No archived jobs found."
                    : "No jobs found. Try loosening filters."}
              </div>
            ) : (
              <div className="list">
                {jobs.map((job) => {
                  const repeatCount = Math.max(0, (job.seen_count ?? 1) - 1);
                  const firstSeenAt = job.first_seen_at || job.scraped_at;
                  const salary = meaningfulSalary(job.salary);
                  return (
                    <article
                      key={job.job_id}
                      className={`row ${job.job_id === selectedId ? "active" : ""}`}
                    >
                      <button
                        className="row-select"
                        type="button"
                        aria-current={job.job_id === selectedId ? "true" : undefined}
                        onClick={() => selectJob(job)}
                      >
                        <div className="title">{job.title}</div>
                        <div className="row-meta">
                          <div className="meta">
                            <span>{job.company}</span>{"\u00a0· "}
                            <span>{jobLocation(job)}</span>
                          </div>
                          {firstSeenAt && <span className="row-first-seen">First seen {formatDate(firstSeenAt)}</span>}
                        </div>
                        {(repeatCount > 0 || (job.posting_count ?? 1) > 1 || job.is_flagged || job.archived_at || salary) && <div className="row-context">
                          {repeatCount > 0 && <span>Repeated {repeatCount} {repeatCount === 1 ? "time" : "times"}</span>}
                          {(job.posting_count ?? 1) > 1 && <span>{job.posting_count} matching postings</span>}
                          {job.is_flagged && <span className="row-flag">Flagged for review</span>}
                          {job.archived_at && <span>Archived</span>}
                          {salary && <span>{salary}</span>}
                        </div>}
                      </button>
                      <button
                        className={`favorite-toggle ${job.is_favorite ? "is-favorite" : ""}`}
                        type="button"
                        aria-label={job.is_favorite ? `Remove ${job.title} from favorites` : `Add ${job.title} to favorites`}
                        aria-pressed={job.is_favorite}
                        title={job.is_favorite ? "Remove from favorites" : "Add to favorites"}
                        disabled={favoriteActionId === job.job_id}
                        onClick={() => toggleFavorite(job)}
                      >
                        <span aria-hidden="true">{job.is_favorite ? "★" : "☆"}</span>
                      </button>
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
        <span className="muted small">Page {pageNumber} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}</span>
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
