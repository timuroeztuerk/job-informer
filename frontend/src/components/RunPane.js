import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { getRunStatus, startRun } from "../api";
const quickActions = [
    {
        mode: "run-once",
        title: "Scrape new jobs",
        description: "Kick off the default job search using the configured keywords and locations.",
        subhead: "Scraper",
    },
    {
        mode: "purge",
        title: "Clean database",
        description: "Apply rule-based filters, then run the AI purge for noisy postings.",
        subhead: "Hygiene",
    },
    {
        mode: "parse-descriptions",
        title: "Parse descriptions",
        description: "Fetch any missing descriptions and parse them with the LLM.",
        subhead: "LLM",
    },
    {
        mode: "reset-ai-purge",
        title: "Reset AI purge",
        description: "Clear AI purge flags so you can re-run the smart filter.",
        subhead: "Maintenance",
    },
    {
        mode: "refetch-titles",
        title: "Fix masked titles",
        description: "Revisit stored job URLs to replace ******** titles/companies with the real names.",
        subhead: "Cleanup",
    },
];
const RunPane = ({ className }) => {
    const [status, setStatus] = useState(null);
    const [loading, setLoading] = useState(false);
    const [loadingMode, setLoadingMode] = useState(null);
    const [error, setError] = useState(null);
    const pollHandle = useRef(null);
    const runIdRef = useRef("");
    const currentRunId = useMemo(() => status?.run_id || "", [status]);
    const modeLabel = (mode) => {
        switch (mode) {
            case "run-once":
                return "Run";
            case "reset-ai-purge":
                return "Reset AI Purge";
            case "parse-descriptions":
                return "Descriptions";
            case "purge":
                return "Purge";
            case "refetch-titles":
                return "Refetch titles";
            default:
                return mode;
        }
    };
    const modeClass = (mode) => `mode-${mode.replace(/[^a-z]/g, "-")}`;
    const stopPolling = () => {
        if (pollHandle.current) {
            window.clearInterval(pollHandle.current);
            pollHandle.current = null;
        }
    };
    const refreshStatus = async () => {
        const runId = runIdRef.current;
        if (!runId)
            return;
        try {
            const nextStatus = await getRunStatus(runId);
            setStatus(nextStatus);
            if (nextStatus.status !== "running") {
                stopPolling();
            }
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load status.");
            stopPolling();
        }
    };
    const startPolling = () => {
        stopPolling();
        pollHandle.current = window.setInterval(refreshStatus, 1500);
    };
    const startCliRun = async (mode) => {
        setLoading(true);
        setLoadingMode(mode);
        setError(null);
        try {
            const nextStatus = await startRun({ mode });
            runIdRef.current = nextStatus.run_id;
            setStatus(nextStatus);
            startPolling();
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed to start run.");
        }
        finally {
            setLoading(false);
            setLoadingMode(null);
        }
    };
    useEffect(() => {
        runIdRef.current = status?.run_id || "";
    }, [status?.run_id]);
    useEffect(() => () => stopPolling(), []);
    const wrapperClassName = ["panel", "run-pane", className].filter(Boolean).join(" ");
    return (_jsxs("section", { className: wrapperClassName, children: [_jsxs("div", { className: "header", children: [_jsx("div", { children: _jsx("p", { className: "label", children: "Run scraper" }) }), _jsx("div", { className: "header-actions", children: _jsx("button", { className: "ghost", type: "button", onClick: refreshStatus, disabled: !currentRunId, children: "Refresh status" }) })] }), _jsxs("div", { className: "quick-actions", children: [_jsxs("div", { className: "qa-head", children: [_jsxs("div", { children: [_jsx("p", { className: "label", children: "Shortcuts" }), _jsx("p", { className: "hint", children: "Launch any command from the backend." })] }), _jsx("span", { className: "hint subtle" })] }), _jsx("div", { className: "qa-grid", children: quickActions.map((action) => (_jsxs("button", { type: "button", className: "qa-card", disabled: loading, onClick: () => startCliRun(action.mode), children: [_jsxs("div", { className: "qa-row", children: [_jsx("span", { className: `pill small mode ${modeClass(action.mode)}`, children: modeLabel(action.mode) }), _jsx("span", { className: `hint ${loadingMode === action.mode ? "bold" : ""}`, children: loadingMode === action.mode ? "Starting…" : action.subhead })] }), _jsx("p", { className: "qa-title", children: action.title }), _jsx("p", { className: "qa-desc", children: action.description })] }, action.mode))) })] }), error && _jsx("div", { className: "error", children: error }), status && (_jsxs("div", { className: "status", children: [_jsxs("div", { className: "status-row", children: [_jsxs("div", { children: [_jsx("p", { className: "label", children: "Run ID" }), _jsxs("div", { className: "id-row", children: [_jsx("code", { children: status.run_id }), _jsx("span", { className: `pill small mode ${modeClass(status.mode)}`, children: modeLabel(status.mode) })] })] }), _jsx("span", { className: `pill ${status.status}`, children: status.status })] }), _jsx("p", { className: "label", children: "Log tail" }), _jsx("pre", { children: status.log_tail || "Waiting for output..." })] }))] }));
};
export default RunPane;
