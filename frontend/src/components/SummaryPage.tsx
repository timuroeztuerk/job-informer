import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDbSummary } from "../api";
import type { CountStat, DbSummary } from "../types";

interface SummaryPageProps {
  className?: string;
}

const SummaryPage: React.FC<SummaryPageProps> = ({ className }) => {
  const [summary, setSummary] = useState<DbSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = useMemo(() => summary?.parsed_insights || null, [summary]);
  const citySummary = useMemo(() => summary?.city_summary || null, [summary]);
  const orphanedJobs = useMemo(
    () =>
      summary?.parsed_descriptions_stats?.orphaned_parsed_descriptions ??
      parsed?.orphaned_jobs ??
      0,
    [parsed, summary]
  );
  const historicalPayloads = useMemo(
    () =>
      parsed?.historical_payloads ??
      summary?.parsed_descriptions_stats?.total_parsed_descriptions ??
      0,
    [parsed, summary]
  );
  const coveragePct = useMemo(() => {
    const totalJobs = summary?.totals.total_jobs || 0;
    if (!totalJobs) return null;
    return ((parsed?.total_records || 0) / totalJobs) * 100;
  }, [parsed, summary]);
  const coverageDisplay = useMemo(() => {
    if (coveragePct === null) return "n/a";
    return `${coveragePct.toFixed(1)}%`;
  }, [coveragePct]);

  const seniorityMix = useMemo(() => parsed?.seniority_mix, [parsed]);
  const seniorSlice = useMemo(() => seniorityMix?.senior, [seniorityMix]);
  const nonSeniorSlice = useMemo(() => seniorityMix?.non_senior, [seniorityMix]);
  const safePct = (value?: number) => (value === undefined || value === null ? "0.0%" : `${value.toFixed(1)}%`);
  const safeYears = (value?: number | null) => (value === null || value === undefined ? "n/a" : `${value.toFixed(1)} yrs`);
  const safeSalary = (value?: number | null) =>
    value === null || value === undefined ? "n/a" : `€${Math.round(value).toLocaleString()}`;

  const shortDate = (value?: string | null) => {
    if (!value) return "n/a";
    return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
  };

  const pct = (entry: CountStat) => `${(entry.percentage ?? 0).toFixed(1)}%`;
  const top = (entries: CountStat[] = [], take = 5) => entries.slice(0, take);
  const barWidth = (entry: CountStat) => `${Math.max(6, Math.min(100, entry.percentage ?? 0))}%`;
  const round = (value: number | null) => (value === null || value === undefined ? "0" : Math.round(value).toLocaleString());

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchDbSummary();
      setSummary(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load summary.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const wrapperClass = ["summary-page", className].filter(Boolean).join(" ");

  return (
    <div className={wrapperClass}>
      <section className="panel intro">
        <div className="intro-row">
          <div>
            <p className="label">Summary</p>
            <h2>Database snapshot</h2>
            <p className="muted">
              Review parsed description trends, coverage, and city distribution for the current jobs in the database.
            </p>
          </div>
          <div className="intro-actions">
            {parsed && (
              <span className="pill small tone">
                Active payloads: {parsed.total_records.toLocaleString()} /{" "}
                {summary?.totals.total_jobs.toLocaleString()}
                {" ("}
                {coverageDisplay}
                {")"}
              </span>
            )}
            <button className="ghost" type="button" onClick={reload} disabled={loading}>
              Refresh
            </button>
          </div>
        </div>

        {error && <div className="error">{error}</div>}
        {!error && loading && <div className="muted">Loading summary...</div>}
        {!error && !loading && summary && (
          <div className="stat-grid">
            <div className="stat-card">
              <p className="label">Total jobs</p>
              <p className="stat-value">{summary.totals.total_jobs.toLocaleString()}</p>
              <p className="muted tiny">All rows currently stored.</p>
            </div>
            <div className="stat-card">
              <p className="label">Last 7 days</p>
              <p className="stat-value">{summary.totals.recent_jobs_7_days.toLocaleString()}</p>
              <p className="muted tiny">Fresh additions in the last week.</p>
            </div>
            <div className="stat-card">
              <p className="label">Date range</p>
              <p className="stat-value small">
                {summary.totals.date_range.earliest ? (
                  <>
                    {shortDate(summary.totals.date_range.earliest)} - {shortDate(summary.totals.date_range.latest)}
                  </>
                ) : (
                  <span>n/a</span>
                )}
              </p>
              <p className="muted tiny">Earliest and latest scrape dates.</p>
            </div>
            {summary.parsed_descriptions_stats && (
              <div className="stat-card">
                <p className="label">Parsed coverage</p>
                <p className="stat-value">
                  {parsed?.total_records.toLocaleString() || "0"} / {summary.totals.total_jobs.toLocaleString()}
                </p>
                <p className="muted tiny">
                  Coverage: {coverageDisplay} · Orphaned payloads: {orphanedJobs.toLocaleString()} · Historical:{" "}
                  {historicalPayloads.toLocaleString()}
                </p>
              </div>
            )}
          </div>
        )}
      </section>

      {parsed && parsed.total_records ? (
        <section className="panel insight-panel">
          <div className="insight-grid">
            <div className="insight">
              <div className="insight-head">
                <p className="label">Programming languages</p>
                <p className="muted tiny">Share of parsed payloads.</p>
              </div>
              <ul>
                {top(parsed.programming_languages, 6).map((item) => (
                  <li key={item.name}>
                    <span className="name">{item.name}</span>
                    <span className="value">
                      {item.count.toLocaleString()}
                      <span className="muted tiny">({pct(item)})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="insight">
              <div className="insight-head">
                <p className="label">Skills</p>
                <p className="muted tiny">Most common skills in parsed text.</p>
              </div>
              <ul>
                {top(parsed.skills, 6).map((item) => (
                  <li key={item.name}>
                    <span className="name">{item.name}</span>
                    <span className="value">
                      {item.count.toLocaleString()}
                      <span className="muted tiny">({pct(item)})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="insight">
              <div className="insight-head">
                <p className="label">Tools</p>
                <p className="muted tiny">Libraries, databases, and platforms.</p>
              </div>
              <ul>
                {top(parsed.tools, 8).map((item) => (
                  <li key={item.name}>
                    <span className="name">{item.name}</span>
                    <span className="value">
                      {item.count.toLocaleString()}
                      <span className="muted tiny">({pct(item)})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="insight">
              <div className="insight-head">
                <p className="label">Degree fields</p>
                <p className="muted tiny">Normalized fields requested.</p>
              </div>
              <ul>
                {top(parsed.degree_fields, 6).map((item) => (
                  <li key={item.name}>
                    <span className="name">{item.name}</span>
                    <span className="value">
                      {item.count.toLocaleString()}
                      <span className="muted tiny">({pct(item)})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="insight">
              <div className="insight-head">
                <p className="label">Seniority</p>
                <p className="muted tiny">Level mix across postings.</p>
              </div>
              <ul>
                {top(parsed.seniority_levels, 4).map((item) => (
                  <li key={item.name}>
                    <span className="name">{item.name}</span>
                    <span className="value">
                      {item.count.toLocaleString()}
                      <span className="muted tiny">({pct(item)})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="insight">
              <div className="insight-head">
                <p className="label">Employment type</p>
                <p className="muted tiny">Contract mix.</p>
              </div>
              <ul>
                {top(parsed.employment_types, 4).map((item) => (
                  <li key={item.name}>
                    <span className="name">{item.name}</span>
                    <span className="value">
                      {item.count.toLocaleString()}
                      <span className="muted tiny">({pct(item)})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>
      ) : summary && !loading ? (
        <section className="panel note">
          <p className="muted">No parsed descriptions yet. Run the description parser to populate this view.</p>
        </section>
      ) : null}

      {parsed && parsed.total_records ? (
        <section className="panel small-grid">
          <div className="stat-card tight">
            <p className="label">Experience requirements</p>
            <p className="stat-value">
              {parsed.experience_years.count ? `${parsed.experience_years.average?.toFixed(1) || "0.0"} years avg` : "n/a"}
            </p>
            <p className="muted tiny">
              {parsed.experience_years.count
                ? `Range: ${parsed.experience_years.min} - ${parsed.experience_years.max} · ${parsed.experience_years.count.toLocaleString()} jobs`
                : "Waiting for parsed experience fields."}
            </p>
          </div>
          <div className="stat-card tight">
            <p className="label">Salary (EUR)</p>
            <p className="stat-value">
              {parsed.salary_eur.count ? `€${round(parsed.salary_eur.average)}` : "n/a"}
            </p>
            <p className="muted tiny">
              {parsed.salary_eur.count
                ? `Range: €${round(parsed.salary_eur.min)} - €${round(parsed.salary_eur.max)} · ${parsed.salary_eur.count.toLocaleString()} jobs`
                : "Waiting for parsed salary ranges."}
            </p>
          </div>
          <div className="stat-card tight">
            <p className="label">Seniority mix</p>
            <p className="stat-value small">
              <span className="block">
                Senior: {seniorSlice?.count?.toLocaleString() || "0"} ({safePct(seniorSlice?.percentage)})
              </span>
              <span className="block">
                Other: {nonSeniorSlice?.count?.toLocaleString() || "0"} ({safePct(nonSeniorSlice?.percentage)})
              </span>
            </p>
            <p className="muted tiny">
              Experience: {safeYears(seniorSlice?.avg_experience)} vs {safeYears(nonSeniorSlice?.avg_experience)}
            </p>
            <p className="muted tiny">Salary: {safeSalary(seniorSlice?.avg_salary)} vs {safeSalary(nonSeniorSlice?.avg_salary)}</p>
          </div>
        </section>
      ) : null}

      {citySummary && (
        <section className="panel city-panel">
          <div className="intro-row">
            <div>
              <p className="label">Cities</p>
              <h3>Top locations</h3>
            </div>
            <span className="pill small tone">Locations scanned: {citySummary.total_jobs.toLocaleString()}</span>
          </div>
          {citySummary.top_cities.length ? (
            <div className="city-list">
              {citySummary.top_cities.map((city, idx) => (
                <div key={city.name} className="city-row">
                  <div className="city-rank">{idx + 1}</div>
                  <div className="city-body">
                    <div className="city-name">{city.name}</div>
                    <div className="muted tiny">
                      {city.count.toLocaleString()} jobs · {pct(city)}
                    </div>
                    <div className="bar">
                      <span style={{ width: barWidth(city) }} />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">No jobs to summarize yet.</p>
          )}
        </section>
      )}
    </div>
  );
};

export default SummaryPage;
