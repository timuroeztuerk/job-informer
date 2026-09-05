import React, { useState } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { archiveJob, restoreJob, setJobFavorite, setJobFlag } from "../../src/api";
import JobDetail from "../../src/components/JobDetail";
import type { Job, JobFlag } from "../../src/types";
vi.mock("../../src/components/JobDescription", () => ({ default: () => null }));

vi.mock("../../src/api", () => ({
  archiveJob: vi.fn(),
  restoreJob: vi.fn(),
  setJobFavorite: vi.fn(),
  setJobFlag: vi.fn(),
}));

const job: Job = {
  job_id: "job/draft",
  title: "Data Scientist",
  company: "Acme",
  location: "Berlin",
  source: "LinkedIn",
  is_favorite: false,
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
    vi.mocked(setJobFavorite).mockReset().mockResolvedValue({
      job_id: job.job_id,
      is_favorite: true,
    });
    vi.mocked(setJobFlag).mockReset().mockImplementation(async (jobId, isFlagged, reason) => ({
      job_id: jobId, is_flagged: isFlagged, flag_reason: isFlagged ? reason?.trim() || null : null,
      flagged_at: isFlagged ? "2026-09-05T08:00:00Z" : null,
    }));
  });

  it("flags immediately, saves an optional reason, and allows unflagging without archiving", async () => {
    const user = userEvent.setup();
    const Detail = () => {
      const [selected, setSelected] = useState(job);
      return <JobDetail job={selected} onArchiveChanged={vi.fn()} onFavoriteChanged={vi.fn()}
        onFlagChanged={(flag) => setSelected((current) => ({ ...current, ...flag }))} />;
    };
    render(<Detail />);
    await user.click(screen.getByRole("button", { name: "Flag" }));
    expect(setJobFlag).toHaveBeenCalledWith(job.job_id, true, undefined);
    expect(await screen.findByRole("button", { name: "Unflag" })).toHaveAttribute("aria-pressed", "true");
    const reason = screen.getByRole("textbox", { name: /Why isn’t this a fit/ });
    // This exercises saving the reason, not per-keystroke behavior.
    await user.click(reason);
    await user.paste("Mostly sales, not analytics.");
    expect(screen.getByRole("status")).toHaveTextContent("Unsaved reason");
    await user.click(screen.getByRole("button", { name: "Save reason" }));
    expect(setJobFlag).toHaveBeenLastCalledWith(job.job_id, true, "Mostly sales, not analytics.");
    expect(await screen.findByText("Reason saved.")).toBeInTheDocument();
    expect(archiveJob).not.toHaveBeenCalled();
    expect(restoreJob).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Unflag" }));
    expect(await screen.findByRole("button", { name: "Flag" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("keeps a failed reason edit available for retry", async () => {
    const user = userEvent.setup();
    vi.mocked(setJobFlag).mockRejectedValueOnce(new Error("Could not save flag"));
    render(<JobDetail job={{ ...job, is_flagged: true, flag_reason: "Old reason" }}
      onArchiveChanged={vi.fn()} onFavoriteChanged={vi.fn()} />);
    const reason = screen.getByRole("textbox");
    await user.clear(reason);
    await user.paste("New reason");
    await user.click(screen.getByRole("button", { name: "Save reason" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save flag");
    expect(reason).toHaveValue("New reason");
    expect(screen.getByRole("button", { name: "Save reason" })).toBeEnabled();
  });

  it("does not carry a pending flag or draft into another job", async () => {
    const user = userEvent.setup();
    let finish!: (flag: JobFlag) => void;
    vi.mocked(setJobFlag).mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const props = { onArchiveChanged: vi.fn(), onFavoriteChanged: vi.fn(), onFlagChanged: vi.fn() };
    const { rerender } = render(<JobDetail job={{ ...job, is_flagged: true, flag_reason: "Old reason" }} {...props} />);
    await user.type(screen.getByRole("textbox"), " updated");
    await user.click(screen.getByRole("button", { name: "Save reason" }));
    rerender(<JobDetail job={{ ...job, job_id: "another", is_flagged: true, flag_reason: "Another reason" }} {...props} />);
    await act(async () => finish({ job_id: job.job_id, is_flagged: true, flag_reason: "Old reason updated", flagged_at: "2026-09-05T08:00:00Z" }));
    expect(screen.getByRole("textbox")).toHaveValue("Another reason");
    expect(screen.queryByText("Reason saved.")).not.toBeInTheDocument();
    expect(props.onFlagChanged).toHaveBeenCalledWith(expect.objectContaining({ job_id: job.job_id }));
  });

  it("shows only useful posting context and clear sighting language", () => {
    const onArchiveChanged = vi.fn();
    const onFavoriteChanged = vi.fn();
    const { rerender } = render(
      <JobDetail
        job={job}
        onArchiveChanged={onArchiveChanged}
        onFavoriteChanged={onFavoriteChanged}
      />
    );

    expect(screen.getByRole("heading", { name: "Data Scientist" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open LinkedIn" })).toHaveAttribute("href", job.url);
    expect(screen.getByText("First seen Jul 9, 2026 · Last seen Jul 12, 2026 · Repeated 3 times")).toBeInTheDocument();
    expect(screen.getByText("Kept as data science: data scientist")).toBeInTheDocument();
    expect(screen.queryByText("Personal notes")).not.toBeInTheDocument();
    expect(screen.queryByText("Found via")).not.toBeInTheDocument();
    expect(screen.queryByText("Why this job is here")).not.toBeInTheDocument();

    rerender(
      <JobDetail
        job={{ ...job, seen_count: 1 }}
        onArchiveChanged={onArchiveChanged}
        onFavoriteChanged={onFavoriteChanged}
      />
    );

    expect(screen.getByText(/Seen once$/)).toBeInTheDocument();
    expect(screen.queryByText(/Repeated 0 times/)).not.toBeInTheDocument();
  });

  it("reports both archive and restore transitions", async () => {
    const user = userEvent.setup();
    const onArchiveChanged = vi.fn();
    const onActionFeedback = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const { rerender } = render(
      <JobDetail
        job={job}
        onArchiveChanged={onArchiveChanged}
        onFavoriteChanged={vi.fn()}
        onActionFeedback={onActionFeedback}
      />
    );
    await user.click(screen.getByRole("button", { name: "Archive" }));

    expect(archiveJob).toHaveBeenCalledWith("job/draft");
    expect(onActionFeedback).toHaveBeenCalledWith("Job archived.");
    expect(onArchiveChanged).toHaveBeenCalledWith(true);

    onArchiveChanged.mockClear();
    rerender(
      <JobDetail
        key="archived"
        job={{ ...job, archived_at: "2026-07-13T08:00:00Z" }}
        onArchiveChanged={onArchiveChanged}
        onFavoriteChanged={vi.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: "Restore" }));

    expect(restoreJob).toHaveBeenCalledWith("job/draft");
    expect(onArchiveChanged).toHaveBeenCalledWith(true);
  });

  it("keeps favorite state independent for active and archived jobs", async () => {
    const user = userEvent.setup();
    const onFavoriteChanged = vi.fn();
    const onArchiveChanged = vi.fn();

    const { rerender } = render(
      <JobDetail
        job={job}
        onArchiveChanged={onArchiveChanged}
        onFavoriteChanged={onFavoriteChanged}
      />
    );
    await user.click(screen.getByRole("button", { name: "Add to favorites" }));

    expect(setJobFavorite).toHaveBeenCalledWith("job/draft", true);
    expect(onFavoriteChanged).toHaveBeenCalledWith("job/draft", true);
    expect(onArchiveChanged).not.toHaveBeenCalled();

    vi.mocked(setJobFavorite).mockClear();
    onFavoriteChanged.mockClear();
    rerender(
      <JobDetail
        job={{ ...job, archived_at: "2026-07-13T08:00:00Z" }}
        onArchiveChanged={onArchiveChanged}
        onFavoriteChanged={onFavoriteChanged}
      />
    );
    await user.click(screen.getByRole("button", { name: "Add to favorites" }));

    expect(setJobFavorite).toHaveBeenCalledWith("job/draft", true);
    expect(onFavoriteChanged).toHaveBeenCalledWith("job/draft", true);
    expect(restoreJob).not.toHaveBeenCalled();
    expect(archiveJob).not.toHaveBeenCalled();
    expect(onArchiveChanged).not.toHaveBeenCalled();
  });
});
