import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JobDescription from "../../src/components/JobDescription";
import DescriptionQueue from "../../src/components/DescriptionQueue";
import { controlDescriptionQueue, fetchDescription, fetchDescriptionQueue, queueDescriptions, reparseDescription, requestDescription } from "../../src/api";
import type { DescriptionQueueState, JobDescriptionResponse } from "../../src/types";

vi.mock("../../src/api", () => ({
  fetchDescription: vi.fn(), requestDescription: vi.fn(), reparseDescription: vi.fn(),
  fetchDescriptionQueue: vi.fn(), queueDescriptions: vi.fn(), controlDescriptionQueue: vi.fn(),
}));

const queue: DescriptionQueueState = { paused: false, reason: null, cooldown_until: null, queued: 0, fetching: 0, saved_jobs: 1, failed_jobs: 0 };
const empty: JobDescriptionResponse = { job_id: "linkedin:123", saved: null, attempts: [], source_versions: 0, reparse_available: false, parser_version: "1.0.0", queue };
const saved: JobDescriptionResponse = {
  ...empty, source_versions: 1, reparse_available: true,
  saved: { source_id: 1, source_url: "https://www.linkedin.com/jobs/view/123/", fetched_at: "2026-09-05T08:00:00Z",
    extraction_id: 1, extractor: "linkedin_public_job", extractor_version: "1.0.0", schema_version: "1", extracted_at: "2026-09-05T08:00:01Z", content_sha256: "hash",
    data: { description_text: "Build reliable models.\n• Python & SQL\n<img src=x onerror=alert(1)>", title: "Data Scientist",
      criteria: [{ key: "employment_type", label: "Employment type", value: "Full-time", evidence: { selector: ".criterion", text: "Full-time" } }],
      evidence: { source_url: "https://www.linkedin.com/jobs/view/123/", description_locator: ".description" } } },
};
const attempt: JobDescriptionResponse["attempts"][number] = { fetch_id: 1, requested_at: "2026-09-05T08:00:00Z", started_at: null, finished_at: null, status: "queued", http_status: null, error_kind: null, error_message: null, source_id: null };

describe("original descriptions", () => {
  beforeEach(() => {
    vi.mocked(fetchDescription).mockReset().mockResolvedValue(empty);
    vi.mocked(requestDescription).mockReset().mockResolvedValue({ ...empty, queue: { ...queue, paused: true, queued: 1 }, attempts: [attempt] });
    vi.mocked(reparseDescription).mockReset().mockResolvedValue(saved);
    vi.mocked(fetchDescriptionQueue).mockReset().mockResolvedValue(queue);
    vi.mocked(queueDescriptions).mockReset().mockResolvedValue({ ...queue, added: 10, queued: 10 });
    vi.mocked(controlDescriptionQueue).mockReset().mockResolvedValue({ ...queue, paused: true });
  });

  it("renders saved text safely and fetches only on explicit action", async () => {
    vi.mocked(fetchDescription).mockResolvedValue(saved);
    const user = userEvent.setup();
    const { container } = render(<JobDescription jobId="linkedin:123" />);
    expect(await screen.findByText("Full-time")).toBeInTheDocument();
    expect(screen.getByText(/Build reliable models/)).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img, script")).toBeNull();
    expect(requestDescription).not.toHaveBeenCalled();
    await user.click(screen.getByText("Saved source & retrieval history"));
    await user.click(screen.getByRole("button", { name: "Reparse saved source" }));
    expect(reparseDescription).toHaveBeenCalledWith("linkedin:123");
    expect(requestDescription).not.toHaveBeenCalled();
  });

  it("queues the selected job once and explains a paused queue", async () => {
    const paused = { ...empty, attempts: [attempt], queue: { ...queue, queued: 1, paused: true } };
    vi.mocked(fetchDescription).mockResolvedValueOnce(empty).mockResolvedValue(paused);
    vi.mocked(requestDescription).mockResolvedValue(paused);
    const user = userEvent.setup();
    const onQueueChanged = vi.fn();
    render(<JobDescription jobId="linkedin:123" onQueueChanged={onQueueChanged} />);
    await user.click(await screen.findByRole("button", { name: "Fetch description" }));
    expect(requestDescription).toHaveBeenCalledWith("linkedin:123", false);
    expect(await screen.findByRole("button", { name: "Queued" })).toBeDisabled();
    expect(screen.getByText(/Use Resume descriptions above/)).toBeInTheDocument();
    expect(onQueueChanged).toHaveBeenCalledOnce();
  });

  it("retains the saved description and offers refresh after a failed attempt", async () => {
    vi.mocked(fetchDescription).mockResolvedValue({ ...saved, attempts: [{ ...attempt, status: "failed", error_kind: "network", error_message: "Request timed out." }] });
    const user = userEvent.setup();
    render(<JobDescription jobId="linkedin:123" />);
    expect(await screen.findByText(/Request timed out. Showing the last saved description/)).toBeInTheDocument();
    expect(screen.getByText(/Build reliable models/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refresh source" }));
    expect(requestDescription).toHaveBeenCalledWith("linkedin:123", true);
  });

  it("does not show an old response after selecting another job", async () => {
    let resolve!: (result: JobDescriptionResponse) => void;
    vi.mocked(fetchDescription).mockReturnValueOnce(new Promise((done) => { resolve = done; })).mockResolvedValue({ ...empty, job_id: "linkedin:456" });
    const { rerender } = render(<JobDescription key="123" jobId="linkedin:123" />);
    rerender(<JobDescription key="456" jobId="linkedin:456" />);
    await screen.findByRole("button", { name: "Fetch description" });
    await act(async () => resolve(saved));
    expect(screen.queryByText(/Build reliable models/)).not.toBeInTheDocument();
  });

  it("shows queue progress, handles pause, and keeps batches explicit", async () => {
    vi.mocked(fetchDescriptionQueue).mockResolvedValue({ ...queue, queued: 2 });
    const user = userEvent.setup();
    const onChanged = vi.fn();
    const { unmount } = render(<DescriptionQueue refreshToken={0} onChanged={onChanged} />);
    await screen.findByText("1 saved · 2 queued");
    expect(queueDescriptions).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Fetch next 10" }));
    expect(queueDescriptions).toHaveBeenCalledWith("next");
    expect(await screen.findByRole("status")).toHaveTextContent("10 descriptions added");
    await user.click(screen.getByRole("button", { name: "Pause descriptions" }));
    expect(controlDescriptionQueue).toHaveBeenCalledWith("pause");
    expect(await screen.findByRole("button", { name: "Resume descriptions" })).toBeEnabled();
    expect(onChanged).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("reports rate-limit cooldown and failed attempts without automatic retries", async () => {
    vi.mocked(fetchDescriptionQueue).mockResolvedValue({ ...queue, paused: true, failed_jobs: 2, queued: 8, reason: "LinkedIn rate limited retrieval.", cooldown_until: "2026-09-05T10:00:00Z" });
    vi.mocked(controlDescriptionQueue).mockRejectedValue(new Error("Retrieval is cooling down."));
    const user = userEvent.setup();
    render(<DescriptionQueue refreshToken={0} onChanged={vi.fn()} />);
    expect(await screen.findByText(/LinkedIn rate limited retrieval/)).toHaveTextContent("Resume after");
    expect(queueDescriptions).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Resume descriptions" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Retrieval is cooling down");
    await user.click(screen.getByRole("button", { name: "Retry failed" }));
    expect(queueDescriptions).toHaveBeenCalledWith("retry");
  });
});
