import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchDbSummary } from "../api";
const SummaryPage = ({ className }) => {
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const parsed = useMemo(() => summary?.parsed_insights || null, [summary]);
    const citySummary = useMemo(() => summary?.city_summary || null, [summary]);
    const orphanedJobs = useMemo(() => summary?.parsed_descriptions_stats?.orphaned_parsed_descriptions ??
        parsed?.orphaned_jobs ??
        0, [parsed, summary]);
    const historicalPayloads = useMemo(() => parsed?.historical_payloads ??
        summary?.parsed_descriptions_stats?.total_parsed_descriptions ??
        0, [parsed, summary]);
    const coveragePct = useMemo(() => {
        const totalJobs = summary?.totals.total_jobs || 0;
        if (!totalJobs)
            return null;
        return ((parsed?.total_records || 0) / totalJobs) * 100;
    }, [parsed, summary]);
    const coverageDisplay = useMemo(() => {
        if (coveragePct === null)
            return "n/a";
        return `${coveragePct.toFixed(1)}%`;
    }, [coveragePct]);
    const seniorityMix = useMemo(() => parsed?.seniority_mix, [parsed]);
    const seniorSlice = useMemo(() => seniorityMix?.senior, [seniorityMix]);
    const nonSeniorSlice = useMemo(() => seniorityMix?.non_senior, [seniorityMix]);
    const safePct = (value) => (value === undefined || value === null ? "0.0%" : `${value.toFixed(1)}%`);
    const safeYears = (value) => (value === null || value === undefined ? "n/a" : `${value.toFixed(1)} yrs`);
    const safeSalary = (value) => value === null || value === undefined ? "n/a" : `€${Math.round(value).toLocaleString()}`;
    const shortDate = (value) => {
        if (!value)
            return "n/a";
        return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
    };
    const pct = (entry) => `${(entry.percentage ?? 0).toFixed(1)}%`;
    const top = (entries = [], take = 5) => entries.slice(0, take);
    const barWidth = (entry) => `${Math.max(6, Math.min(100, entry.percentage ?? 0))}%`;
    const round = (value) => (value === null || value === undefined ? "0" : Math.round(value).toLocaleString());
    const reload = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const data = await fetchDbSummary();
            setSummary(data);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load summary.");
        }
        finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => {
        reload();
    }, [reload]);
    const wrapperClass = ["summary-page", className].filter(Boolean).join(" ");
    return (_jsxs("div", { className: wrapperClass, children: [_jsxs("section", { className: "panel intro", children: [_jsxs("div", { className: "intro-row", children: [_jsxs("div", { children: [_jsx("p", { className: "label", children: "Summary" }), _jsx("h2", { children: "Database snapshot" }), _jsx("p", { className: "muted", children: "Review parsed description trends, coverage, and city distribution for the current jobs in the database." })] }), _jsxs("div", { className: "intro-actions", children: [parsed && (_jsxs("span", { className: "pill small tone", children: ["Active payloads: ", parsed.total_records.toLocaleString(), " /", " ", summary?.totals.total_jobs.toLocaleString(), " (", coverageDisplay, ")"] })), _jsx("button", { className: "ghost", type: "button", onClick: reload, disabled: loading, children: "Refresh" })] })] }), error && _jsx("div", { className: "error", children: error }), !error && loading && _jsx("div", { className: "muted", children: "Loading summary..." }), !error && !loading && summary && (_jsxs("div", { className: "stat-grid", children: [_jsxs("div", { className: "stat-card", children: [_jsx("p", { className: "label", children: "Total jobs" }), _jsx("p", { className: "stat-value", children: summary.totals.total_jobs.toLocaleString() }), _jsx("p", { className: "muted tiny", children: "All rows currently stored." })] }), _jsxs("div", { className: "stat-card", children: [_jsx("p", { className: "label", children: "Last 7 days" }), _jsx("p", { className: "stat-value", children: summary.totals.recent_jobs_7_days.toLocaleString() }), _jsx("p", { className: "muted tiny", children: "Fresh additions in the last week." })] }), _jsxs("div", { className: "stat-card", children: [_jsx("p", { className: "label", children: "Date range" }), _jsx("p", { className: "stat-value small", children: summary.totals.date_range.earliest ? (_jsxs(_Fragment, { children: [shortDate(summary.totals.date_range.earliest), " - ", shortDate(summary.totals.date_range.latest)] })) : (_jsx("span", { children: "n/a" })) }), _jsx("p", { className: "muted tiny", children: "Earliest and latest scrape dates." })] }), summary.parsed_descriptions_stats && (_jsxs("div", { className: "stat-card", children: [_jsx("p", { className: "label", children: "Parsed coverage" }), _jsxs("p", { className: "stat-value", children: [parsed?.total_records.toLocaleString() || "0", " / ", summary.totals.total_jobs.toLocaleString()] }), _jsxs("p", { className: "muted tiny", children: ["Coverage: ", coverageDisplay, " \u00B7 Orphaned payloads: ", orphanedJobs.toLocaleString(), " \u00B7 Historical:", " ", historicalPayloads.toLocaleString()] })] }))] }))] }), parsed && parsed.total_records ? (_jsx("section", { className: "panel insight-panel", children: _jsxs("div", { className: "insight-grid", children: [_jsxs("div", { className: "insight", children: [_jsxs("div", { className: "insight-head", children: [_jsx("p", { className: "label", children: "Programming languages" }), _jsx("p", { className: "muted tiny", children: "Share of parsed payloads." })] }), _jsx("ul", { children: top(parsed.programming_languages, 6).map((item) => (_jsxs("li", { children: [_jsx("span", { className: "name", children: item.name }), _jsxs("span", { className: "value", children: [item.count.toLocaleString(), _jsxs("span", { className: "muted tiny", children: ["(", pct(item), ")"] })] })] }, item.name))) })] }), _jsxs("div", { className: "insight", children: [_jsxs("div", { className: "insight-head", children: [_jsx("p", { className: "label", children: "Skills" }), _jsx("p", { className: "muted tiny", children: "Most common skills in parsed text." })] }), _jsx("ul", { children: top(parsed.skills, 6).map((item) => (_jsxs("li", { children: [_jsx("span", { className: "name", children: item.name }), _jsxs("span", { className: "value", children: [item.count.toLocaleString(), _jsxs("span", { className: "muted tiny", children: ["(", pct(item), ")"] })] })] }, item.name))) })] }), _jsxs("div", { className: "insight", children: [_jsxs("div", { className: "insight-head", children: [_jsx("p", { className: "label", children: "Tools" }), _jsx("p", { className: "muted tiny", children: "Libraries, databases, and platforms." })] }), _jsx("ul", { children: top(parsed.tools, 8).map((item) => (_jsxs("li", { children: [_jsx("span", { className: "name", children: item.name }), _jsxs("span", { className: "value", children: [item.count.toLocaleString(), _jsxs("span", { className: "muted tiny", children: ["(", pct(item), ")"] })] })] }, item.name))) })] }), _jsxs("div", { className: "insight", children: [_jsxs("div", { className: "insight-head", children: [_jsx("p", { className: "label", children: "Degree fields" }), _jsx("p", { className: "muted tiny", children: "Normalized fields requested." })] }), _jsx("ul", { children: top(parsed.degree_fields, 6).map((item) => (_jsxs("li", { children: [_jsx("span", { className: "name", children: item.name }), _jsxs("span", { className: "value", children: [item.count.toLocaleString(), _jsxs("span", { className: "muted tiny", children: ["(", pct(item), ")"] })] })] }, item.name))) })] }), _jsxs("div", { className: "insight", children: [_jsxs("div", { className: "insight-head", children: [_jsx("p", { className: "label", children: "Seniority" }), _jsx("p", { className: "muted tiny", children: "Level mix across postings." })] }), _jsx("ul", { children: top(parsed.seniority_levels, 4).map((item) => (_jsxs("li", { children: [_jsx("span", { className: "name", children: item.name }), _jsxs("span", { className: "value", children: [item.count.toLocaleString(), _jsxs("span", { className: "muted tiny", children: ["(", pct(item), ")"] })] })] }, item.name))) })] }), _jsxs("div", { className: "insight", children: [_jsxs("div", { className: "insight-head", children: [_jsx("p", { className: "label", children: "Employment type" }), _jsx("p", { className: "muted tiny", children: "Contract mix." })] }), _jsx("ul", { children: top(parsed.employment_types, 4).map((item) => (_jsxs("li", { children: [_jsx("span", { className: "name", children: item.name }), _jsxs("span", { className: "value", children: [item.count.toLocaleString(), _jsxs("span", { className: "muted tiny", children: ["(", pct(item), ")"] })] })] }, item.name))) })] })] }) })) : summary && !loading ? (_jsx("section", { className: "panel note", children: _jsx("p", { className: "muted", children: "No parsed descriptions yet. Run the description parser to populate this view." }) })) : null, parsed && parsed.total_records ? (_jsxs("section", { className: "panel small-grid", children: [_jsxs("div", { className: "stat-card tight", children: [_jsx("p", { className: "label", children: "Experience requirements" }), _jsx("p", { className: "stat-value", children: parsed.experience_years.count ? `${parsed.experience_years.average?.toFixed(1) || "0.0"} years avg` : "n/a" }), _jsx("p", { className: "muted tiny", children: parsed.experience_years.count
                                    ? `Range: ${parsed.experience_years.min} - ${parsed.experience_years.max} · ${parsed.experience_years.count.toLocaleString()} jobs`
                                    : "Waiting for parsed experience fields." })] }), _jsxs("div", { className: "stat-card tight", children: [_jsx("p", { className: "label", children: "Salary (EUR)" }), _jsx("p", { className: "stat-value", children: parsed.salary_eur.count ? `€${round(parsed.salary_eur.average)}` : "n/a" }), _jsx("p", { className: "muted tiny", children: parsed.salary_eur.count
                                    ? `Range: €${round(parsed.salary_eur.min)} - €${round(parsed.salary_eur.max)} · ${parsed.salary_eur.count.toLocaleString()} jobs`
                                    : "Waiting for parsed salary ranges." })] }), _jsxs("div", { className: "stat-card tight", children: [_jsx("p", { className: "label", children: "Seniority mix" }), _jsxs("p", { className: "stat-value small", children: [_jsxs("span", { className: "block", children: ["Senior: ", seniorSlice?.count?.toLocaleString() || "0", " (", safePct(seniorSlice?.percentage), ")"] }), _jsxs("span", { className: "block", children: ["Other: ", nonSeniorSlice?.count?.toLocaleString() || "0", " (", safePct(nonSeniorSlice?.percentage), ")"] })] }), _jsxs("p", { className: "muted tiny", children: ["Experience: ", safeYears(seniorSlice?.avg_experience), " vs ", safeYears(nonSeniorSlice?.avg_experience)] }), _jsxs("p", { className: "muted tiny", children: ["Salary: ", safeSalary(seniorSlice?.avg_salary), " vs ", safeSalary(nonSeniorSlice?.avg_salary)] })] })] })) : null, citySummary && (_jsxs("section", { className: "panel city-panel", children: [_jsxs("div", { className: "intro-row", children: [_jsxs("div", { children: [_jsx("p", { className: "label", children: "Cities" }), _jsx("h3", { children: "Top locations" })] }), _jsxs("span", { className: "pill small tone", children: ["Locations scanned: ", citySummary.total_jobs.toLocaleString()] })] }), citySummary.top_cities.length ? (_jsx("div", { className: "city-list", children: citySummary.top_cities.map((city, idx) => (_jsxs("div", { className: "city-row", children: [_jsx("div", { className: "city-rank", children: idx + 1 }), _jsxs("div", { className: "city-body", children: [_jsx("div", { className: "city-name", children: city.name }), _jsxs("div", { className: "muted tiny", children: [city.count.toLocaleString(), " jobs \u00B7 ", pct(city)] }), _jsx("div", { className: "bar", children: _jsx("span", { style: { width: barWidth(city) } }) })] })] }, city.name))) })) : (_jsx("p", { className: "muted", children: "No jobs to summarize yet." }))] }))] }));
};
export default SummaryPage;
