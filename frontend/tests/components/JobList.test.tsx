import React, { useRef, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { archiveJob, fetchJob, fetchJobs } from "../../src/api";
import type { Job, JobsResponse } from "../../src/types";
import JobDetail from "../../src/components/JobDetail";
import JobTable, { type JobTableHandle } from "../../src/components/JobTable";

vi.mock("../../src/api", () => ({
  archiveJob: vi.fn(),
  fetchJob: vi.fn(),
  fetchJobs: vi.fn(),
  restoreJob: vi.fn(),
}));

const makeJob = (job_id: string, title: string): Job => ({
  job_id,
  title,
  company: `${title} Co`,
  location: "Berlin",
  source: "LinkedIn",
  scraped_at: "2026-07-12T08:00:00Z",
});

const firstJob = makeJob("job-1", "First role");
const secondJob = makeJob("job-2", "Second role");
const response = (items: Job[]): JobsResponse => ({ total: items.length, count: items.length, items });

const JobList: React.FC = () => {
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const tableRef = useRef<JobTableHandle | null>(null);

  return (
    <div>
      <JobTable ref={tableRef} companies={[]} onSelect={setSelectedJob} />
      <JobDetail
        job={selectedJob}
        onArchiveChanged={() => tableRef.current?.reload(false)}
      />
    </div>
  );
};

describe("JobTable and JobDetail", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/?view=dashboard&status=unreviewed&priority=high");
    vi.mocked(archiveJob).mockReset().mockResolvedValue(undefined);
    vi.mocked(fetchJobs).mockReset();
    vi.mocked(fetchJob).mockReset().mockImplementation(async (jobId) => {
      const match = [firstJob, secondJob].find((job) => job.job_id === jobId);
      if (!match) throw new Error("Unknown job");
      return match;
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("selects jobs and advances after archiving without note filters", async () => {
    vi.mocked(fetchJobs)
      .mockResolvedValueOnce(response([firstJob, secondJob]))
      .mockResolvedValueOnce(response([secondJob]));
    const user = userEvent.setup();

    render(<JobList />);

    const detail = document.querySelector<HTMLElement>(".detail-pane");
    if (!detail) throw new Error("Detail pane did not render");
    await within(detail).findByRole("heading", { name: "First role" });
    expect(vi.mocked(fetchJobs).mock.calls[0][0]).not.toHaveProperty("annotationStatus");
    expect(vi.mocked(fetchJobs).mock.calls[0][0]).not.toHaveProperty("annotationPriority");

    await user.click(screen.getByRole("button", { name: /Second role/ }));
    await within(detail).findByRole("heading", { name: "Second role" });
    expect(new URLSearchParams(window.location.search).get("job")).toBe("job-2");

    await user.click(screen.getByRole("button", { name: /First role/ }));
    await within(detail).findByRole("heading", { name: "First role" });
    await user.click(within(detail).getByRole("button", { name: "Archive" }));

    await waitFor(() => {
      expect(archiveJob).toHaveBeenCalledWith("job-1");
      expect(fetchJobs).toHaveBeenCalledTimes(2);
    });
    await within(detail).findByRole("heading", { name: "Second role" });
    expect(screen.queryByRole("button", { name: /First role/ })).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("job")).toBe("job-2");
    expect(new URLSearchParams(window.location.search).has("status")).toBe(false);
    expect(new URLSearchParams(window.location.search).has("priority")).toBe(false);
  });

  it("shows one canonical card with its repeat count and latest repeat date", async () => {
    const repeatedJob: Job = {
      ...firstJob,
      seen_count: 4,
      first_seen_at: "2026-07-09T08:00:00Z",
      last_seen_at: "2026-07-12T08:00:00Z",
    };
    vi.mocked(fetchJobs).mockResolvedValue(response([repeatedJob]));

    render(<JobTable companies={[]} onSelect={vi.fn()} />);

    expect(await screen.findByText("Repeated 3 times")).toBeInTheDocument();
    expect(screen.getByText("Latest repeat Jul 12, 2026")).toBeInTheDocument();
    expect(screen.getByText("First seen Jul 9, 2026")).toBeInTheDocument();
    expect(document.querySelectorAll(".job-table .row")).toHaveLength(1);
  });

  it("filters by role family and the query group that found the job", async () => {
    vi.mocked(fetchJobs).mockResolvedValue(response([firstJob]));
    const user = userEvent.setup();

    render(
      <JobTable
        companies={[]}
        roleFamilies={["analytics_bi"]}
        queryGroups={[{ key: "data_science", name: "Data science" }]}
        onSelect={vi.fn()}
      />
    );

    await screen.findByRole("button", { name: /First role/ });
    const filterToggle = screen.getByText("Open filters").closest("summary");
    if (!filterToggle) throw new Error("Filter toggle did not render");
    await user.click(filterToggle);
    expect(screen.queryByLabelText("Source")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Company"), "Acme");
    await user.selectOptions(screen.getByLabelText("Role family"), "analytics_bi");
    await user.selectOptions(screen.getByLabelText("Found via"), "data_science");

    await waitFor(() => {
      expect(fetchJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ company: "Acme", roleFamily: "analytics_bi", queryGroup: "data_science" })
      );
    });
    await user.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(document.querySelector(".filter-menu")).not.toHaveAttribute("open"));
    const params = new URLSearchParams(window.location.search);
    expect(params.get("company")).toBe("Acme");
    expect(params.get("role")).toBe("analytics_bi");
    expect(params.get("query_group")).toBe("data_science");
  });
});
