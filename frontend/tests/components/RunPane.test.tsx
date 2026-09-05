import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getRunStatus, listRuns, startRun } from "../../src/api";
import RunPane, { type RunPaneHandle } from "../../src/components/RunPane";
import type { CliMode, RunStatus } from "../../src/types";

vi.mock("../../src/api", () => ({
  getRunStatus: vi.fn(),
  listRuns: vi.fn(),
  startRun: vi.fn(),
}));

const completedRun = (mode: CliMode): RunStatus => ({
  run_id: `run-${mode}`,
  mode,
  status: "succeeded",
  started_at: "2026-07-13T08:00:00Z",
  finished_at: "2026-07-13T08:01:00Z",
  log_tail: "Done",
});

const runningRun = (status: "starting" | "running"): RunStatus => ({
  run_id: "run-collection",
  mode: "run-once",
  status,
  started_at: "2026-07-13T08:00:00Z",
  log_tail: status === "running" ? "Searching configured sources" : "",
});

const structuredRunningRun = (): RunStatus => ({
  ...runningRun("running"),
  log_tail: "raw terminal line",
  progress: {
    stage: "collecting",
    label: "Searching configured sources",
    current_query: "LinkedIn · Data in Berlin",
    completed_queries: 1,
    total_queries: 3,
    metrics: { observed: 12, new: 3, archived: 1 },
    updated_at: "2026-07-13T08:00:10Z",
    events: [
      { at: "2026-07-13T08:00:00Z", level: "info", message: "Starting source collection" },
      { at: "2026-07-13T08:00:10Z", level: "info", message: "Searching LinkedIn · Data in Berlin" },
    ],
  },
});

const comparedRun = (): RunStatus => ({
  ...completedRun("run-once"),
  metrics: { observed: 11, new: 4, archived: 2 },
  scope_comparison: {
    countrywide: {
      locations: ["Germany", "Switzerland"],
      queries: 4,
      unique_jobs: 7,
      pages_attempted: 8,
      pages_completed: 7,
      page_completion_percent: 87.5,
      request_failures: 1,
    },
    cities: {
      locations: ["Berlin", "Stuttgart", "Frankfurt", "München"],
      queries: 8,
      unique_jobs: 6,
      pages_attempted: 16,
      pages_completed: 16,
      page_completion_percent: 100,
      request_failures: 0,
    },
    shared_jobs: 5,
    countrywide_only_jobs: 2,
    city_only_jobs: 1,
    city_jobs_covered_by_countrywide_percent: 83.3,
  },
});

describe("RunPane actions", () => {
  beforeEach(() => {
    vi.mocked(getRunStatus).mockReset();
    vi.mocked(listRuns).mockReset().mockResolvedValue([]);
    vi.mocked(startRun).mockReset().mockImplementation(async ({ mode = "run-once" }) => completedRun(mode));
  });

  it("keeps the single collection action prominent", async () => {
    const user = userEvent.setup();
    render(<RunPane />);

    expect(screen.getByRole("button", { name: "Collect jobs" })).toBeInTheDocument();
    expect(screen.queryByText("Maintenance")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Collect jobs" }));
    expect(startRun).toHaveBeenCalledWith({ mode: "run-once" });
  });

  it("can start collection from the review-home entry point", async () => {
    const ref = React.createRef<RunPaneHandle>();
    render(<RunPane ref={ref} />);

    ref.current?.startCollection();

    await waitFor(() => expect(startRun).toHaveBeenCalledWith({ mode: "run-once" }));
  });

  it("keeps polling and reports progress when a collection begins in starting state", async () => {
    const user = userEvent.setup();
    vi.mocked(startRun).mockResolvedValue(runningRun("starting"));
    vi.mocked(getRunStatus).mockResolvedValue(runningRun("running"));

    render(<RunPane />);
    await user.click(screen.getByRole("button", { name: "Collect jobs" }));

    expect(await screen.findByText("Collecting jobs…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Collecting…" })).toBeDisabled();
    expect(getRunStatus).toHaveBeenCalledWith("run-collection");
  });

  it("resumes and reports an active collection that started outside this page", async () => {
    vi.mocked(listRuns).mockResolvedValue([runningRun("running")]);
    vi.mocked(getRunStatus).mockResolvedValue(runningRun("running"));
    const onRunStatusChange = vi.fn();

    render(<RunPane onRunStatusChange={onRunStatusChange} />);

    expect(await screen.findByText("Collecting jobs…")).toBeInTheDocument();
    expect(getRunStatus).toHaveBeenCalledWith("run-collection");
    expect(onRunStatusChange).toHaveBeenCalledWith(expect.objectContaining({ run_id: "run-collection", status: "running" }));
  });

  it("shows only compact structured progress", async () => {
    const user = userEvent.setup();
    vi.mocked(startRun).mockResolvedValue(structuredRunningRun());
    vi.mocked(getRunStatus).mockResolvedValue(structuredRunningRun());
    render(<RunPane />);

    await user.click(screen.getByRole("button", { name: "Collect jobs" }));

    expect(await screen.findByText(/LinkedIn · Data in Berlin/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("max", "3");
    expect(screen.getByText("Searching configured sources")).toBeInTheDocument();
    expect(screen.queryByText("Technical log")).not.toBeInTheDocument();
    expect(screen.queryByText("Query diagnostics")).not.toBeInTheDocument();
    expect(screen.queryByText("run-collection")).not.toBeInTheDocument();
  });

  it("keeps the latest country-versus-city comparison visible", async () => {
    vi.mocked(listRuns).mockResolvedValue([comparedRun()]);

    render(<RunPane />);

    expect(await screen.findByText("Country-wide vs retained cities")).toBeInTheDocument();
    expect(screen.getByText("Germany + Switzerland")).toBeInTheDocument();
    expect(screen.getByText("Berlin + Stuttgart + Frankfurt + München")).toBeInTheDocument();
    expect(screen.getByText("7 unique jobs")).toBeInTheDocument();
    expect(screen.getByText("6 unique jobs")).toBeInTheDocument();
    expect(screen.getByText("5 shared · 83.3% of city jobs covered country-wide")).toBeInTheDocument();
    expect(screen.getByText("2 country-only · 1 city-only")).toBeInTheDocument();
    expect(screen.getByText("7/8 pages · 1 request failure")).toBeInTheDocument();
    expect(screen.getByText("16/16 pages · 0 request failures")).toBeInTheDocument();
  });

});
