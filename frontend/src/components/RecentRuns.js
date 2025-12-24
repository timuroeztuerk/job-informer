import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { listRuns } from "../api";
const modeLabel = (mode) => {
    switch (mode) {
        case "run-once":
            return "Run";
        case "reset-ai-purge":
            return "Reset AI purge";
        case "parse-descriptions":
            return "Parse descriptions";
        case "db-summary":
            return "DB summary";
        case "test":
            return "Test";
        case "purge":
            return "Purge (rules + AI)";
        default:
            return mode;
    }
};
const modeClass = (mode) => `mode-${mode.replace(/[^a-z]/g, "-")}`;
const formatDate = (value) => {
    if (!value)
        return "n/a";
    return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
};
const RecentRuns = ({ className }) => {
    const [runs, setRuns] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            setRuns(await listRuns(5));
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load runs.");
        }
        finally {
            setLoading(false);
        }
    };
    useEffect(() => {
        load();
    }, []);
    const wrapperClassName = ["panel", "recent-runs", className].filter(Boolean).join(" ");
    return (_jsxs("section", { className: wrapperClassName, children: [_jsxs("div", { className: "header", children: [_jsxs("div", { children: [_jsx("p", { className: "label", children: "Recent runs" }), _jsx("h3", { children: "Latest activity" })] }), _jsx("button", { className: "ghost", type: "button", onClick: load, disabled: loading, children: "Refresh" })] }), error ? (_jsx("div", { className: "error", children: error })) : loading ? (_jsx("div", { className: "muted", children: "Loading\u2026" })) : !runs.length ? (_jsx("div", { className: "muted", children: "No runs yet." })) : (_jsx("div", { className: "list", children: runs.map((run) => (_jsxs("div", { className: "row", children: [_jsxs("div", { className: "top", children: [_jsx("span", { className: `pill small mode ${modeClass(run.mode)}`, children: modeLabel(run.mode) }), _jsx("span", { className: `pill small ${run.status}`, children: run.status })] }), _jsx("p", { className: "muted small", children: formatDate(run.started_at) }), _jsx("p", { className: "muted tiny", children: run.run_id.slice(0, 10) })] }, run.run_id))) }))] }));
};
export default RecentRuns;
