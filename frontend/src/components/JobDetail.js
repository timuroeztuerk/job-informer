import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { deleteJob, fetchJob } from "../api";
const JobDetail = ({ job, onDeleted }) => {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [details, setDetails] = useState(null);
    const [parsed, setParsed] = useState(null);
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
                if (cancelled)
                    return;
                setDetails(fullJob);
                setParsed(fullJob.parsed_description?.payload || null);
            }
            catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : "Failed to load job.");
                }
            }
            finally {
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
    const formatDate = (value) => {
        if (!value)
            return "n/a";
        return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
    };
    const salaryText = (range) => {
        if (!range)
            return "unspecified";
        const { min, max } = range;
        if (min && max)
            return `${min.toLocaleString()} – ${max.toLocaleString()}`;
        if (min)
            return `${min.toLocaleString()}+`;
        if (max)
            return `up to ${max.toLocaleString()}`;
        return "unspecified";
    };
    const benefitsText = (benefits) => {
        if (!benefits)
            return "unspecified";
        if (Array.isArray(benefits))
            return benefits.length ? benefits.join(", ") : "unspecified";
        return benefits;
    };
    const confirmDelete = async () => {
        if (!details || deleting)
            return;
        const ok = window.confirm("Delete this job? This cannot be undone.");
        if (!ok)
            return;
        setDeleting(true);
        try {
            await deleteJob(details.job_id);
            setDetails(null);
            setParsed(null);
            onDeleted();
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to delete job.");
        }
        finally {
            setDeleting(false);
        }
    };
    return (_jsxs("div", { className: "panel detail-pane", children: [_jsx("p", { className: "label", children: "Details" }), !job && _jsx("div", { children: "Select a job to see details." }), job && (_jsxs("div", { className: "card", children: [_jsxs("header", { children: [_jsxs("div", { children: [_jsx("p", { className: "company", children: job.company }), _jsx("h3", { children: job.title }), _jsxs("p", { className: "muted", children: [job.location, " \u2022 ", job.source] })] }), _jsxs("div", { className: "header-actions", children: [job.url && (_jsx("a", { className: "link", href: job.url, target: "_blank", rel: "noreferrer", children: "Open posting" })), _jsx("button", { className: "danger", type: "button", onClick: confirmDelete, disabled: deleting, children: "Delete" })] })] }), error && _jsx("div", { className: "error", children: error }), !error && loading && _jsx("div", { className: "muted", children: "Loading details\u2026" }), !error && !loading && details && (_jsxs(_Fragment, { children: [_jsxs("p", { className: "muted", children: ["Scraped ", formatDate(details.scraped_at)] }), parsed && (_jsxs("div", { className: "parsed", children: [_jsxs("div", { className: "pill-row", children: [_jsx("span", { className: "pill", children: parsed.seniority || "unspecified" }), _jsx("span", { className: "pill", children: parsed.employment_type || "unspecified" }), _jsx("span", { className: "pill", children: parsed.remote || "unspecified" }), _jsx("span", { className: "pill", children: parsed.location?.[0] || job.location })] }), _jsxs("div", { className: "summary", children: [_jsx("p", { className: "summary-title", children: "Summary" }), parsed.summary ? _jsx("p", { className: "body", children: parsed.summary }) : _jsx("p", { className: "muted", children: "No parsed summary yet." })] }), _jsxs("div", { className: "grid", children: [_jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Languages" }), _jsxs("div", { className: "chip-row", children: [(parsed.languages || []).map((lang) => (_jsx("span", { className: "chip", children: lang }, lang))), (!parsed.languages || !parsed.languages.length) && _jsx("span", { className: "muted small", children: "n/a" })] })] }), _jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Programming" }), _jsxs("div", { className: "chip-row", children: [(parsed.programming_languages || []).map((lang) => (_jsx("span", { className: "chip", children: lang }, lang))), (!parsed.programming_languages || !parsed.programming_languages.length) && _jsx("span", { className: "muted small", children: "n/a" })] })] }), _jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Tools" }), _jsxs("div", { className: "chip-row", children: [(parsed.tools || []).map((tool) => (_jsx("span", { className: "chip", children: tool }, tool))), (!parsed.tools || !parsed.tools.length) && _jsx("span", { className: "muted small", children: "n/a" })] })] }), _jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Skills" }), _jsxs("div", { className: "chip-row", children: [(parsed.skills || []).map((skill) => (_jsx("span", { className: "chip", children: skill }, skill))), (!parsed.skills || !parsed.skills.length) && _jsx("span", { className: "muted small", children: "n/a" })] })] })] }), _jsxs("div", { className: "meta", children: [_jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Degree" }), _jsxs("p", { className: "muted", children: [parsed.degree_field || "unspecified", " \u2022 ", parsed.degree_type || "unspecified"] })] }), _jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Experience" }), _jsxs("p", { className: "muted", children: [parsed.years_experience_min ?? "unspecified", "+ years"] })] }), _jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Salary (EUR)" }), _jsx("p", { className: "muted", children: salaryText(parsed.salary_eur_range) })] }), _jsxs("div", { children: [_jsx("p", { className: "summary-title", children: "Benefits" }), _jsx("p", { className: "muted", children: benefitsText(parsed["extra benefits"]) })] })] })] })), details.description ? (_jsxs("details", { className: "raw", children: [_jsx("summary", { children: "Full description" }), _jsx("p", { className: "body", children: details.description })] })) : (_jsx("p", { className: "muted", children: "No description stored." }))] }))] }))] }));
};
export default JobDetail;
