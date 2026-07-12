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
      annotationStatus: "unreviewed",
      annotationPriority: "",
      dateFrom: "2026-07-01",
      dateTo: "2026-07-12",
      sort: "fit_score_desc",
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
      annotation_status: "unreviewed",
      date_from: "2026-07-01T00:00:00",
      date_to: "2026-07-12T23:59:59",
      sort: "fit_score_desc",
    });
    expect(url.searchParams.has("source")).toBe(false);
    expect(url.searchParams.has("annotation_priority")).toBe(false);
  });
});
