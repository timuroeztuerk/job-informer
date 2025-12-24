import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useState, } from "react";
import { fetchJobs } from "../api";
const PAGE_SIZE = 5;
const JobTable = forwardRef(({ companies, onSelect }, ref) => {
    const [jobs, setJobs] = useState([]);
    const [total, setTotal] = useState(0);
    const [offset, setOffset] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [company, setCompany] = useState("");
    const [sort, setSort] = useState("scraped_at_desc");
    const [selectedId, setSelectedId] = useState(null);
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
                if (cancelled)
                    return;
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
                }
                else {
                    setSelectedId(data.items[0].job_id);
                    onSelect(data.items[0]);
                }
            }
            catch (err) {
                if (cancelled)
                    return;
                setError(err instanceof Error ? err.message : "Failed to load jobs.");
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
    }, [offset, company, sort, reloadToken]);
    useImperativeHandle(ref, () => ({
        reload: (reset = true) => {
            if (reset) {
                setOffset(0);
            }
            setReloadToken((token) => token + 1);
        },
    }));
    const selectJob = (job) => {
        setSelectedId(job?.job_id || null);
        onSelect(job);
    };
    const nextPage = () => {
        if (!hasNext)
            return;
        setOffset((value) => value + PAGE_SIZE);
    };
    const prevPage = () => {
        setOffset((value) => Math.max(0, value - PAGE_SIZE));
    };
    const formatDate = (value) => {
        if (!value)
            return "n/a";
        return new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(value));
    };
    const clearFilters = () => {
        setCompany("");
        setSort("scraped_at_desc");
        setOffset(0);
        setReloadToken((token) => token + 1);
    };
    return (_jsxs("section", { className: "panel job-table", children: [_jsx("div", { className: "table-header", children: _jsxs("div", { children: [_jsx("p", { className: "label", children: "Jobs" }), _jsxs("h3", { children: [total ? total.toLocaleString() : "No", " records"] })] }) }), _jsxs("div", { className: "filters inline", children: [_jsxs("label", { className: "tiny", children: [_jsx("span", { children: "Company" }), _jsxs("select", { value: company, className: "input", onChange: (e) => {
                                    setCompany(e.target.value);
                                    setOffset(0);
                                    setReloadToken((token) => token + 1);
                                }, children: [_jsx("option", { value: "", children: "Any company" }), companyOptions.map((c) => (_jsx("option", { value: c, children: c }, c)))] })] }), _jsxs("label", { className: "tiny", children: [_jsx("span", { children: "Sort" }), _jsxs("select", { value: sort, className: "input", onChange: (e) => {
                                    setSort(e.target.value);
                                    setOffset(0);
                                    setReloadToken((token) => token + 1);
                                }, children: [_jsx("option", { value: "scraped_at_desc", children: "Newest" }), _jsx("option", { value: "scraped_at_asc", children: "Oldest" }), _jsx("option", { value: "title_asc", children: "Title A-Z" }), _jsx("option", { value: "title_desc", children: "Title Z-A" })] })] }), _jsx("button", { className: "ghost sm", type: "button", onClick: clearFilters, disabled: loading, children: "Clear" })] }), _jsx("div", { className: "table-card", children: error ? (_jsxs("div", { className: "error", children: [error, _jsx("button", { className: "ghost sm", type: "button", onClick: () => setReloadToken((token) => token + 1), disabled: loading, children: "Retry" })] })) : (_jsx(_Fragment, { children: loading ? (_jsx("div", { className: "skeletons", children: Array.from({ length: 5 }).map((_, idx) => (_jsx("div", { className: "skeleton-row" }, idx))) })) : jobs.length === 0 ? (_jsx("div", { className: "empty", children: "No jobs found. Try loosening filters." })) : (_jsx("div", { className: "list", children: jobs.map((job) => (_jsxs("article", { className: `row ${job.job_id === selectedId ? "active" : ""}`, onClick: () => selectJob(job), children: [_jsx("div", { className: "title", children: job.title }), _jsxs("div", { className: "meta", children: [_jsx("span", { children: job.company }), _jsx("span", { children: "\u2022" }), _jsx("span", { children: job.location })] }), _jsxs("div", { className: "tags", children: [_jsx("span", { className: "tag", children: job.source }), job.salary ? _jsx("span", { className: "tag soft", children: job.salary }) : null, _jsx("span", { className: "tag soft", children: formatDate(job.scraped_at) })] })] }, job.job_id))) })) })) }), _jsxs("div", { className: "pagination", children: [_jsx("button", { className: "ghost", type: "button", disabled: offset === 0 || loading, onClick: prevPage, children: "Prev" }), _jsxs("span", { className: "label", children: ["Page ", pageNumber] }), _jsx("button", { className: "ghost", type: "button", disabled: !hasNext || loading, onClick: nextPage, children: "Next" })] })] }));
});
JobTable.displayName = "JobTable";
export default JobTable;
