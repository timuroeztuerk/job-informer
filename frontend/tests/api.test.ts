import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchJobs } from "../src/api";

describe("API parameter serialization", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
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
      archived: "exclude",
      date_from: "2026-07-01T00:00:00",
      date_to: "2026-07-12T23:59:59",
      sort: "last_seen_desc",
    });
    expect(url.searchParams.has("source")).toBe(false);
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
