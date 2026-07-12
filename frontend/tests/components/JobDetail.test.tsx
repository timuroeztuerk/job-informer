import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchJob } from "../../src/api";
import type { Job, JobAnnotation } from "../../src/types";
import JobDetail from "../../src/components/JobDetail";

vi.mock("../../src/api", () => ({
  archiveJob: vi.fn(),
  fetchJob: vi.fn(),
  restoreJob: vi.fn(),
  updateJobAnnotation: vi.fn(),
}));

const serverAnnotation: JobAnnotation = {
  status: "unreviewed",
  priority: "medium",
  notes: "Saved on the server",
  why_interesting: "",
  skill_gaps: [],
  follow_up_date: null,
  resume_version: "",
};

const job: Job = {
  job_id: "job/draft",
  title: "Platform Engineer",
  company: "Acme",
  location: "Berlin",
  source: "example",
  annotation: serverAnnotation,
};

describe("annotation draft recovery", () => {
  beforeEach(() => {
    vi.mocked(fetchJob).mockReset().mockResolvedValue(job);
  });

  it("restores a valid job-specific local draft over the server annotation", async () => {
    const draftAnnotation: JobAnnotation = {
      ...serverAnnotation,
      status: "interesting",
      priority: "high",
      notes: "Recovered locally",
      why_interesting: "Strong systems work",
      skill_gaps: ["Kubernetes", "Go"],
      resume_version: "platform-v3",
    };
    window.localStorage.setItem(
      "job-informer.annotation-draft.job%2Fdraft",
      JSON.stringify({
        version: 1,
        jobId: job.job_id,
        annotation: draftAnnotation,
        skillGapInput: "Kubernetes, Go, observability",
        savedAt: "2026-07-12T08:30:00.000Z",
      })
    );

    render(<JobDetail job={job} onArchiveChanged={vi.fn()} onAnnotationSaved={vi.fn()} />);
    await screen.findByText(/Draft kept locally/);
    await userEvent.click(screen.getByText("Personal notes"));

    expect(screen.getByLabelText("Review status")).toHaveValue("interesting");
    expect(screen.getByLabelText("Priority")).toHaveValue("high");
    expect(screen.getByLabelText("Why interesting")).toHaveValue("Strong systems work");
    expect(screen.getByLabelText(/^Skill gaps/)).toHaveValue("Kubernetes, Go, observability");
    expect(screen.getByLabelText("Notes")).toHaveValue("Recovered locally");
    expect(screen.getByLabelText("Resume version")).toHaveValue("platform-v3");
  });
});
