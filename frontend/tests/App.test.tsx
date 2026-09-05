import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
vi.mock("../src/components/DescriptionQueue", async () => {
  const React = await import("react");
  return { default: ({ onActivityChange }: { onActivityChange: (message: string) => void }) => {
    React.useEffect(() => { onActivityChange("2 descriptions pending"); }, [onActivityChange]);
    return <button>Fetch all</button>;
  } };
});
vi.mock("../src/components/ExtractionQueue", () => ({ default: () => null }));
vi.mock("../src/components/JobTable", async () => {
  const React = await import("react");
  return { default: React.forwardRef(() => null) };
});
vi.mock("../src/components/RunPane", async () => {
  const React = await import("react");
  return { default: React.forwardRef(() => null) };
});
vi.mock("../src/components/SummaryPage", () => ({ default: () => null }));

const emptyStats: JobStats = {
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
    window.history.replaceState({}, "", "/");
    vi.mocked(fetchStats).mockReset().mockResolvedValue(emptyStats);
  });

  it("shows a prominent warning when the connected database is empty", async () => {
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Connected to an empty database");
    expect(screen.getByRole("alert")).toHaveTextContent("job-informer-desktop");
    expect(screen.getByRole("alert")).toHaveTextContent("/app/data/jobs.db");
    expect(await screen.findByText("API connected")).toBeInTheDocument();
    expect(screen.getByText("LinkedIn not collected yet")).toBeInTheDocument();
  });

  it("marks the API as disconnected and offers a retry when stats cannot load", async () => {
    vi.mocked(fetchStats).mockRejectedValue(new TypeError("Failed to fetch"));

    render(<App />);

    expect(await screen.findByText("API disconnected")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load stats from the API");
    expect(screen.getByRole("button", { name: "Retry connection" })).toBeEnabled();
  });

  it("uses the single jobs view without a separate review home", async () => {
    render(<App />);

    expect(await screen.findByRole("button", { name: "Jobs" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Review" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Intelligence" })).toBeInTheDocument();
  });

  it("tucks processing controls away while keeping background activity visible", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText("2 descriptions pending")).toBeVisible();
    const tools = document.querySelector(".workspace-tools");
    expect(tools).not.toHaveAttribute("open");
    expect(screen.getByText("Fetch all")).not.toBeVisible();
    await user.click(screen.getByText("Collection & processing"));
    expect(screen.getByRole("button", { name: "Fetch all" })).toBeVisible();
    await user.click(screen.getByText("Collection & processing"));
    expect(screen.getByText("Fetch all")).not.toBeVisible();
    expect(screen.getByText("2 descriptions pending")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Intelligence" }));
    await user.click(screen.getByRole("button", { name: "Jobs" }));
    expect(tools).not.toHaveAttribute("open");
    expect(screen.getByText("2 descriptions pending")).toBeVisible();
  });

  it("keeps collection freshness in the compact API line", async () => {
    vi.mocked(fetchStats).mockResolvedValue({
      ...emptyStats,
      collection_freshness: {
        as_of: new Date().toISOString(),
        last_collected_at: new Date().toISOString(),
        latest_observation_at: new Date().toISOString(),
        latest_scrape_at: new Date().toISOString(),
        source: "job_observation",
        age_days: 0.1,
        status: "fresh",
      },
    });

    render(<App />);

    expect(await screen.findByText("LinkedIn updated 2h ago")).toBeInTheDocument();
    expect(screen.queryByText("Collection freshness")).not.toBeInTheDocument();
  });
});
