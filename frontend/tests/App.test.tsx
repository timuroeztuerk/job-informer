import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { fetchStats } from "../src/api";
import type { JobStats } from "../src/types";

vi.mock("../src/api", () => ({
  API_BASE: "/",
  fetchStats: vi.fn(),
  getApiErrorMessage: (_error: unknown, fallback: string) => `${fallback} Check VITE_API_BASE.`,
}));
vi.mock("../src/components/JobDetail", () => ({ default: () => null }));
vi.mock("../src/components/JobTable", () => ({ default: () => null }));
vi.mock("../src/components/RecentRuns", () => ({ default: () => null }));
vi.mock("../src/components/ReviewHome", () => ({ default: () => null }));
vi.mock("../src/components/RunPane", async () => {
  const React = await import("react");
  return { default: React.forwardRef(() => null) };
});
vi.mock("../src/components/SummaryPage", () => ({ default: () => null }));

const emptyStats: JobStats = {
  total_jobs: 0,
  jobs_by_source: {},
  top_companies: {},
  recent_jobs_7_days: 0,
  date_range: { earliest: null, latest: null },
  database: {
    status: "ready",
    instance_name: "job-informer-desktop",
    db_path: "/app/data/jobs.db",
    schema_ok: true,
    missing_tables: [],
    total_jobs: 0,
    active_jobs: 0,
    db_size_bytes: 4096,
    db_modified_at: "2026-07-12T12:00:00Z",
  },
};

describe("database diagnostics", () => {
  beforeEach(() => {
    vi.mocked(fetchStats).mockReset().mockResolvedValue(emptyStats);
  });

  it("shows a prominent warning when the connected database is empty", async () => {
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Connected to an empty database");
    expect(screen.getByRole("alert")).toHaveTextContent("job-informer-desktop");
    expect(screen.getByRole("alert")).toHaveTextContent("/app/data/jobs.db");
    await screen.findByText("API / · connected");
    expect(screen.getByRole("status")).toHaveTextContent("API / · connected");
  });

  it("marks the API as disconnected and offers a retry when stats cannot load", async () => {
    vi.mocked(fetchStats).mockRejectedValue(new TypeError("Failed to fetch"));

    render(<App />);

    await screen.findByText("API / · disconnected");
    expect(screen.getByRole("status")).toHaveTextContent("API / · disconnected");
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load stats from the API");
    expect(screen.getByRole("button", { name: "Retry connection" })).toBeEnabled();
  });
});
