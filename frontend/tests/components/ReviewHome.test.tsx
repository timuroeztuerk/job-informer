import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJobs } from "../../src/api";
import ReviewHome from "../../src/components/ReviewHome";
import type { Job, JobsResponse } from "../../src/types";

vi.mock("../../src/api", () => ({
  fetchJobs: vi.fn(),
  getApiErrorMessage: (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback,
}));

const job = (overrides: Partial<Job> & Pick<Job, "job_id" | "title">): Job => ({
  company: "Example GmbH",
  location: "Berlin",
  source: "example",
  ...overrides,
});

const response = (items: Job[]): JobsResponse => ({ total: items.length, count: items.length, items });

describe("ReviewHome", () => {
  beforeEach(() => {
    vi.mocked(fetchJobs).mockReset();
  });

  it("loads operator queues and opens the selected job with truthful filters", async () => {
    const topMatch = job({ job_id: "fit-1", title: "Platform engineer" });
    const due = job({
      job_id: "due-1",
      title: "Data engineer",
      annotation: {
        status: "interesting",
        priority: "high",
        notes: "",
        why_interesting: "",
        skill_gaps: [],
        follow_up_date: "2026-07-12",
        resume_version: "",
      },
    });
    const recent = job({ job_id: "new-1", title: "ML engineer", scraped_at: "2026-07-11T08:00:00Z" });
    const recurring = job({ job_id: "repeat-1", title: "Backend engineer", seen_count: 4 });

    vi.mocked(fetchJobs)
      .mockResolvedValueOnce(response([topMatch]))
      .mockResolvedValueOnce(response([due]))
      .mockResolvedValueOnce(response([recent]))
      .mockResolvedValueOnce(response([recurring]));
    const onOpenDashboard = vi.fn();
    const onCollect = vi.fn();
    const user = userEvent.setup();

    render(
      <ReviewHome
        now={new Date(2026, 6, 13, 12)}
        onCollect={onCollect}
        onOpenDashboard={onOpenDashboard}
      />
    );

    expect(await screen.findByRole("heading", { name: "Best-fit unreviewed jobs" })).toBeInTheDocument();
    expect(fetchJobs).toHaveBeenCalledWith(expect.objectContaining({ annotationStatus: "unreviewed", sort: "fit_score_desc" }));
    expect(fetchJobs).toHaveBeenCalledWith(expect.objectContaining({ dateFrom: "2026-07-07", sort: "last_seen_desc" }));
    expect(screen.getByText("Due Jul 12, 2026")).toBeInTheDocument();
    expect(screen.getByText("Seen 4×")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Platform engineer/ }));
    expect(onOpenDashboard).toHaveBeenCalledWith({ job: "fit-1" });

    await user.click(screen.getByRole("button", { name: "Collect now" }));
    expect(onCollect).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "Review all" }));
    expect(onOpenDashboard).toHaveBeenCalledWith({ annotationStatus: "unreviewed", sort: "fit_score_desc" });
  });

  it("offers a retry when a queue request fails", async () => {
    vi.mocked(fetchJobs).mockRejectedValue(new Error("API unavailable"));
    const user = userEvent.setup();

    render(<ReviewHome onCollect={vi.fn()} onOpenDashboard={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("API unavailable");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchJobs).toHaveBeenCalledTimes(8));
  });
});
