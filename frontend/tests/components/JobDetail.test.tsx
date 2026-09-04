import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { archiveJob, restoreJob } from "../../src/api";
import JobDetail from "../../src/components/JobDetail";
import type { Job } from "../../src/types";

vi.mock("../../src/api", () => ({
  archiveJob: vi.fn(),
  restoreJob: vi.fn(),
}));

const job: Job = {
  job_id: "job/draft",
  title: "Data Scientist",
  company: "Acme",
  location: "Berlin",
  source: "LinkedIn",
  url: "https://www.linkedin.com/jobs/view/1/",
  first_seen_at: "2026-07-09T08:00:00Z",
  last_seen_at: "2026-07-12T08:00:00Z",
  seen_count: 4,
  relevance_reason: "Kept as data science: data scientist",
};

describe("minimal job details", () => {
  beforeEach(() => {
    vi.mocked(archiveJob).mockReset().mockResolvedValue(undefined);
    vi.mocked(restoreJob).mockReset().mockResolvedValue(undefined);
  });

  it("shows only the useful posting, sightings, and relevance context", () => {
    render(<JobDetail job={job} onArchiveChanged={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Data Scientist" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open LinkedIn" })).toHaveAttribute("href", job.url);
    expect(screen.getByText("First seen Jul 9, 2026 · Last seen Jul 12, 2026 · Repeated 3 times")).toBeInTheDocument();
    expect(screen.getByText("Kept as data science: data scientist")).toBeInTheDocument();
    expect(screen.queryByText("Personal notes")).not.toBeInTheDocument();
    expect(screen.queryByText("Found via")).not.toBeInTheDocument();
    expect(screen.queryByText("Why this job is here")).not.toBeInTheDocument();
  });

  it("reports archive completion before the selected job is removed", async () => {
    const user = userEvent.setup();
    const onArchiveChanged = vi.fn();
    const onActionFeedback = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <JobDetail
        job={job}
        onArchiveChanged={onArchiveChanged}
        onActionFeedback={onActionFeedback}
      />
    );
    await user.click(screen.getByRole("button", { name: "Archive" }));

    expect(archiveJob).toHaveBeenCalledWith("job/draft");
    expect(onActionFeedback).toHaveBeenCalledWith("Job archived.");
    expect(onArchiveChanged).toHaveBeenCalledWith(true);
  });

  it("restores an archived job directly", async () => {
    const user = userEvent.setup();
    const onArchiveChanged = vi.fn();

    render(<JobDetail job={{ ...job, archived_at: "2026-07-13T08:00:00Z" }} onArchiveChanged={onArchiveChanged} />);
    await user.click(screen.getByRole("button", { name: "Restore" }));

    expect(restoreJob).toHaveBeenCalledWith("job/draft");
    expect(onArchiveChanged).toHaveBeenCalledWith(true);
  });
});
