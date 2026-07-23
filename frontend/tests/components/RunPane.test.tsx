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
    current_source: "LinkedIn · Data in Berlin",
    completed_sources: 1,
    total_sources: 3,
    metrics: { observed: 12, new: 3, archived: 1, descriptions_fetched: 0, parsed: 0 },
    updated_at: "2026-07-13T08:00:10Z",
    events: [
      { at: "2026-07-13T08:00:00Z", level: "info", message: "Starting source collection" },
      { at: "2026-07-13T08:00:10Z", level: "info", message: "Searching LinkedIn · Data in Berlin" },
    ],
  },
});

describe("RunPane actions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(getRunStatus).mockReset();
    vi.mocked(listRuns).mockReset().mockResolvedValue([]);
    vi.mocked(startRun).mockReset().mockImplementation(async ({ mode = "run-once" }) => completedRun(mode));
  });

  it("keeps collection prominent and maintenance collapsed by default", async () => {
    const user = userEvent.setup();
    render(<RunPane />);

    expect(screen.getByRole("button", { name: "Collect now" })).toBeInTheDocument();
    const maintenance = screen.getByText("Infrequent cleanup, parsing, and repair tools.").closest("details");
    expect(maintenance).not.toHaveAttribute("open");

    await user.click(screen.getByRole("button", { name: "Collect now" }));
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
    await user.click(screen.getByRole("button", { name: "Collect now" }));

    expect(await screen.findByText("Collection in progress")).toBeInTheDocument();
    expect(screen.getByText("Collecting and recording live output", { exact: false })).toBeInTheDocument();
    expect(getRunStatus).toHaveBeenCalledWith("run-collection");
  });

  it("resumes and reports an active collection that started outside this page", async () => {
    vi.mocked(listRuns).mockResolvedValue([runningRun("running")]);
    vi.mocked(getRunStatus).mockResolvedValue(runningRun("running"));
    const onRunStatusChange = vi.fn();

    render(<RunPane onRunStatusChange={onRunStatusChange} />);

    expect(await screen.findByText("Collection in progress")).toBeInTheDocument();
    expect(getRunStatus).toHaveBeenCalledWith("run-collection");
    expect(onRunStatusChange).toHaveBeenCalledWith(expect.objectContaining({ run_id: "run-collection", status: "running" }));
  });

  it("shows structured collection progress before the collapsed technical log", async () => {
    const user = userEvent.setup();
    vi.mocked(startRun).mockResolvedValue(structuredRunningRun());
    vi.mocked(getRunStatus).mockResolvedValue(structuredRunningRun());
    render(<RunPane />);

    await user.click(screen.getByRole("button", { name: "Collect now" }));

    expect(await screen.findByText("LinkedIn · Data in Berlin")).toBeInTheDocument();
    expect(screen.getByText("Sources 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("max", "3");
    expect(screen.getByText("Searching LinkedIn · Data in Berlin")).toBeInTheDocument();
    const technicalLog = screen.getByText("Technical log").closest("details");
    expect(technicalLog).not.toHaveAttribute("open");

    await user.click(screen.getByText("Technical log").closest("summary")!);
    expect(technicalLog).toHaveAttribute("open");
    expect(screen.getByText("raw terminal line")).toBeInTheDocument();
  });

  it("requires confirmation before cleaning the database", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RunPane />);

    await user.click(screen.getByText("Infrequent cleanup, parsing, and repair tools.").closest("summary")!);
    await user.click(screen.getByRole("button", { name: /Clean database/ }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Matching postings may be archived"));
    expect(startRun).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: /Clean database/ }));
    expect(startRun).toHaveBeenCalledWith({ mode: "purge" });
  });

  it("requires confirmation before resetting AI purge flags", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RunPane />);

    await user.click(screen.getByText("Infrequent cleanup, parsing, and repair tools.").closest("summary")!);
    await user.click(screen.getByRole("button", { name: /Reset AI purge/ }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("eligible for AI filtering again"));
    expect(startRun).toHaveBeenCalledWith({ mode: "reset-ai-purge" });
  });
});
