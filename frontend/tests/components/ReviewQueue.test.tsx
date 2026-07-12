import React, { useRef, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJob, fetchJobs, updateJobAnnotation } from "../../src/api";
import type { Job, JobAnnotation, JobsResponse } from "../../src/types";
import JobDetail from "../../src/components/JobDetail";
import JobTable, { type JobTableHandle } from "../../src/components/JobTable";

vi.mock("../../src/api", () => ({
  archiveJob: vi.fn(),
  fetchJob: vi.fn(),
  fetchJobs: vi.fn(),
  restoreJob: vi.fn(),
  updateJobAnnotation: vi.fn(),
}));

const unreviewed: JobAnnotation = {
  status: "unreviewed",
  priority: "medium",
  notes: "",
  why_interesting: "",
  skill_gaps: [],
  follow_up_date: null,
  resume_version: "",
};

const makeJob = (job_id: string, title: string): Job => ({
  job_id,
  title,
  company: `${title} Co`,
  location: "Berlin",
  source: "example",
  scraped_at: "2026-07-12T08:00:00Z",
  annotation: { ...unreviewed },
});

const firstJob = makeJob("job-1", "First role");
const secondJob = makeJob("job-2", "Second role");
const response = (items: Job[]): JobsResponse => ({ total: items.length, count: items.length, items });

const ReviewQueue: React.FC = () => {
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const tableRef = useRef<JobTableHandle | null>(null);

  return (
    <div>
      <JobTable ref={tableRef} sources={[]} companies={[]} onSelect={setSelectedJob} />
      <JobDetail
        job={selectedJob}
        onArchiveChanged={() => undefined}
        onAnnotationSaved={() => tableRef.current?.reload(false)}
      />
    </div>
  );
};

describe("review queue", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/?view=dashboard&status=unreviewed");
    vi.mocked(fetchJobs).mockReset();
    vi.mocked(fetchJob).mockReset().mockImplementation(async (jobId) => {
      const match = [firstJob, secondJob].find((job) => job.job_id === jobId);
      if (!match) throw new Error("Unknown job");
      return match;
    });
    vi.mocked(updateJobAnnotation).mockReset();
  });

  it("selects review items and advances after a saved decision refreshes the queue", async () => {
    vi.mocked(fetchJobs)
      .mockResolvedValueOnce(response([firstJob, secondJob]))
      .mockResolvedValueOnce(response([secondJob]));
    vi.mocked(updateJobAnnotation).mockImplementation(async (_jobId, annotation) => ({
      ...annotation,
      updated_at: "2026-07-12T09:00:00Z",
    }));
    const user = userEvent.setup();

    render(<ReviewQueue />);

    const detail = document.querySelector<HTMLElement>(".detail-pane");
    if (!detail) throw new Error("Detail pane did not render");
    await within(detail).findByRole("heading", { name: "First role" });
    expect(fetchJobs).toHaveBeenCalledWith(expect.objectContaining({ annotationStatus: "unreviewed" }));

    await user.click(screen.getByRole("button", { name: /Second role/ }));
    await within(detail).findByRole("heading", { name: "Second role" });
    expect(new URLSearchParams(window.location.search).get("job")).toBe("job-2");

    await user.click(screen.getByRole("button", { name: /First role/ }));
    await within(detail).findByRole("heading", { name: "First role" });
    await user.click(within(detail).getByText("Personal notes"));
    await user.selectOptions(within(detail).getByLabelText("Review status"), "interesting");
    await user.type(within(detail).getByLabelText("Notes"), "  Worth pursuing  ");
    await user.click(within(detail).getByRole("button", { name: "Save notes" }));

    await waitFor(() => {
      expect(updateJobAnnotation).toHaveBeenCalledWith(
        "job-1",
        expect.objectContaining({ status: "interesting", notes: "Worth pursuing" })
      );
      expect(fetchJobs).toHaveBeenCalledTimes(2);
    });
    await within(detail).findByRole("heading", { name: "Second role" });
    expect(screen.queryByRole("button", { name: /First role/ })).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("job")).toBe("job-2");
  });
});
