// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchDbSummary, fetchJobs, setJobFavorite, setJobFlag, requestDescription, fetchDescription, reparseDescription, queueDescriptions, controlDescriptionQueue } from "../src/api";

describe("API parameter serialization", () => {
  beforeEach(() => {
    // URL construction needs only an origin; these request/response tests need no DOM.
    vi.stubGlobal("window", { location: { origin: "http://localhost:3000" } });
  });
  it("requests the selected Intelligence window", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response("{}", {
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    await fetchDbSummary("30d");
    const url = new URL(fetchSpy.mock.calls[0][0]);
    expect(url.pathname).toBe("/db-summary");
    expect(url.searchParams.get("window")).toBe("30d");
  });
  it("keeps description reads, refreshes, reparsing, and queue controls distinct", async () => {
    const fetchSpy = vi.fn().mockImplementation(async () => new Response("{}", { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchSpy);
    await fetchDescription("linkedin:123");
    await requestDescription("linkedin:123", true);
    await reparseDescription("linkedin:123");
    await queueDescriptions("retry");
    await controlDescriptionQueue("pause");
    expect(fetchSpy.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
      "/jobs/linkedin%3A123/description", "/jobs/linkedin%3A123/description", "/jobs/linkedin%3A123/description/reparse", "/descriptions/queue", "/descriptions/queue/pause",
    ]);
    expect(fetchSpy.mock.calls[0][1].method).toBeUndefined();
    expect(JSON.parse(fetchSpy.mock.calls[1][1].body)).toEqual({ refresh: true });
    expect(JSON.parse(fetchSpy.mock.calls[3][1].body)).toEqual({ selection: "retry", limit: 10 });
    expect(fetchSpy.mock.calls[4][1].method).toBe("POST");
    await queueDescriptions();
    expect(new URL(fetchSpy.mock.calls[5][0]).pathname).toBe("/descriptions/queue");
    expect(JSON.parse(fetchSpy.mock.calls[5][1].body)).toEqual({ selection: "all" });
  });
  it("serializes filters, date boundaries, pagination, and omitted empty values", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ total: 0, count: 0, items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchSpy);

    await fetchJobs({
      limit: 5,
      offset: 10,
      search: "C++ & platform",
      location: "Berlin",
      source: "",
      company: "ACME/Tools",
      companyExact: true,
      locationPrimary: true,
      lastSeenFrom: "2026-07-01T12:00:00Z",
      repeated: true,
      relevanceOutcome: "unmatched",
      favorite: true,
      flagged: true,
      archived: "exclude",
      dateFrom: "2026-07-01",
      dateTo: "2026-07-12",
      sort: "last_seen_desc",
    });

    const [requestUrl] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const url = new URL(requestUrl);
    const params: Record<string, string> = {};
    url.searchParams.forEach((value, key) => {
      params[key] = value;
    });
    expect(url.pathname).toBe("/jobs");
    expect(params).toEqual({
      limit: "5",
      offset: "10",
      search: "C++ & platform",
      location: "Berlin",
      company: "ACME/Tools",
      company_exact: "true",
      location_primary: "true",
      last_seen_from: "2026-07-01T12:00:00Z",
      repeated: "true",
      relevance_outcome: "unmatched",
      favorite: "true",
      flagged: "true",
      archived: "exclude",
      date_from: "2026-07-01T00:00:00",
      date_to: "2026-07-12T23:59:59",
      sort: "last_seen_desc",
    });
    expect(url.searchParams.has("source")).toBe(false);
  });

  it("saves a flag reason against the encoded job id", async () => {
    const result = { job_id: "job/draft", is_flagged: true, flag_reason: "Mostly sales", flagged_at: "2026-09-05T08:00:00Z" };
    const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), {
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchSpy);
    await expect(setJobFlag("job/draft", true, "Mostly sales")).resolves.toEqual(result);
    const [url, request] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(new URL(url).pathname).toBe("/jobs/job%2Fdraft/flag");
    expect(request.method).toBe("PUT");
    expect(JSON.parse(request.body as string)).toEqual({ is_flagged: true, flag_reason: "Mostly sales" });
  });

  it("updates one favorite with an explicit boolean payload", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ job_id: "job/draft", is_favorite: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchSpy);

    await expect(setJobFavorite("job/draft", true)).resolves.toEqual({
      job_id: "job/draft",
      is_favorite: true,
    });

    const [requestUrl, request] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(new URL(requestUrl).pathname).toBe("/jobs/job%2Fdraft/favorite");
    expect(request.method).toBe("PUT");
    expect(request.body).toBe(JSON.stringify({ is_favorite: true }));
  });

  it("turns an HTML fallback response into an actionable API configuration error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><html><body>Vite app</body></html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        })
      )
    );

    await expect(fetchJobs()).rejects.toThrow(/returned text\/html instead of JSON.*VITE_API_BASE/);
  });

  it("reports malformed JSON without exposing the browser parse error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("{not-json", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
    );

    await expect(fetchJobs()).rejects.toThrow(/API returned invalid JSON.*VITE_API_BASE/);
  });

  it("turns network failures into an actionable connection error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(fetchJobs()).rejects.toThrow(/Could not connect to the API.*backend is running.*VITE_API_BASE/);
  });
});
