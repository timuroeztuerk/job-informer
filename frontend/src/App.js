import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import JobDetail from "./components/JobDetail";
import JobTable from "./components/JobTable";
import RunPane from "./components/RunPane";
import SummaryPage from "./components/SummaryPage";
import { fetchStats } from "./api";
const App = () => {
    const [stats, setStats] = useState(null);
    const [statsError, setStatsError] = useState(null);
    const [selectedJob, setSelectedJob] = useState(null);
    const [viewMode, setViewMode] = useState("dashboard");
    const [now, setNow] = useState(new Date());
    const tableRef = useRef(null);
    const apiBase = import.meta.env.VITE_API_BASE || "http://localhost:8000";
    useEffect(() => {
        let cancelled = false;
        const loadStats = async () => {
            try {
                const data = await fetchStats();
                if (!cancelled) {
                    setStats(data);
                }
            }
            catch (err) {
                if (!cancelled) {
                    setStatsError("Could not load stats (check API is up and token matches).");
                }
            }
        };
        loadStats();
        const timer = window.setInterval(() => setNow(new Date()), 30000);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, []);
    const formattedNow = useMemo(() => new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(now), [now]);
    const handleDeleted = () => {
        setSelectedJob(null);
        tableRef.current?.reload?.(true);
    };
    return (_jsxs("div", { className: "page", children: [_jsxs("header", { className: "hero", children: [_jsxs("div", { className: "hero-left", children: [_jsx("h1", { children: "Job Informer" }), _jsxs("div", { className: "hero-meta", children: [_jsx("span", { className: "eyebrow", children: formattedNow }), _jsx("p", { className: "muted small", children: "Scrape, parse, and keep an eye on the database footprint." })] })] }), _jsxs("div", { className: "hero-actions", children: [_jsxs("div", { className: "badge", children: [_jsx("span", { className: "dot" }), "API ", apiBase] }), _jsxs("div", { className: "view-toggle", children: [_jsx("button", { type: "button", className: viewMode === "dashboard" ? "active" : "", onClick: () => setViewMode("dashboard"), children: "Dashboard" }), _jsx("button", { type: "button", className: viewMode === "summary" ? "active" : "", onClick: () => setViewMode("summary"), children: "Summary" })] })] })] }), statsError && (_jsxs("div", { className: "stat warning", children: [_jsx("p", { className: "label", children: "Stats" }), _jsx("p", { className: "value", children: statsError })] })), viewMode === "dashboard" ? (_jsx("main", { className: "layout", children: _jsxs("div", { className: "main", children: [_jsx(RunPane, { className: "highlight" }), _jsxs("div", { className: "jobs", children: [_jsx(JobTable, { ref: tableRef, onSelect: setSelectedJob, sources: stats?.sources_list || [], companies: stats?.companies_list || [] }), _jsx(JobDetail, { job: selectedJob, onDeleted: handleDeleted })] })] }) })) : (_jsx(SummaryPage, { className: "summary-shell" }))] }));
};
export default App;
