const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const API_TOKEN = import.meta.env.VITE_API_TOKEN;
function authHeaders(extra) {
    const headers = extra ? { ...extra } : {};
    if (API_TOKEN) {
        headers["x-api-token"] = API_TOKEN;
    }
    return headers;
}
function buildUrl(path, params) {
    const url = new URL(path, API_BASE.endsWith("/") ? API_BASE : `${API_BASE}/`);
    if (params) {
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== "") {
                url.searchParams.set(key, String(value));
            }
        });
    }
    return url.toString();
}
async function handleJson(res) {
    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Request failed with status ${res.status}`);
    }
    return res.json();
}
export async function startRun(payload) {
    const res = await fetch(buildUrl("runs"), {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
    });
    return handleJson(res);
}
export async function getRunStatus(runId) {
    const res = await fetch(buildUrl(`runs/${runId}`), { headers: authHeaders() });
    return handleJson(res);
}
export async function listRuns(limit = 20) {
    const res = await fetch(buildUrl("runs", { limit }), { headers: authHeaders() });
    return handleJson(res);
}
export async function fetchJobs(query = {}) {
    const res = await fetch(buildUrl("jobs", {
        limit: query.limit ?? 25,
        offset: query.offset ?? 0,
        search: query.search,
        location: query.location,
        source: query.source,
        company: query.company,
        date_from: query.dateFrom,
        date_to: query.dateTo,
        sort: query.sort,
    }));
    return handleJson(res);
}
export async function fetchJob(jobId) {
    const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), { headers: authHeaders() });
    return handleJson(res);
}
export async function deleteJob(jobId) {
    const res = await fetch(buildUrl(`jobs/${encodeURIComponent(jobId)}`), {
        method: "DELETE",
        headers: authHeaders(),
    });
    if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `Delete failed with status ${res.status}`);
    }
}
export async function fetchStats() {
    const res = await fetch(buildUrl("stats"), { headers: authHeaders() });
    return handleJson(res);
}
export async function fetchDbSummary() {
    const res = await fetch(buildUrl("db-summary"), { headers: authHeaders() });
    return handleJson(res);
}
