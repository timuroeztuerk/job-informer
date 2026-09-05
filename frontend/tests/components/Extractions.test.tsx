import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExtractionQueue from "../../src/components/ExtractionQueue";
import JobExtraction from "../../src/components/JobExtraction";
import { controlAIQueue, fetchAIQueue, fetchJobExtraction, queueAI } from "../../src/api";
import type { AIJobResult, AIQueueState } from "../../src/types";

vi.mock("../../src/api", () => ({ fetchAIQueue: vi.fn(), fetchJobExtraction: vi.fn(), queueAI: vi.fn(), controlAIQueue: vi.fn() }));

const queue: AIQueueState = { configured: true, paused: false, reason: null, cooldown_until: 0, pilot_limit: 10,
  estimated_cost_usd: 0, counts: {}, jobs: [], usage: { input_tokens: 0, output_tokens: 0, cached_tokens: 0, reasoning_tokens: 0 },
  model: "gpt-5.4-mini", reasoning: "medium", service_tier: "flex" };
const empty: AIJobResult = { job_id: "linkedin:123", status: null, error: null, stale: false,
  queue: { paused: false, cooldown_until: 0 }, source: null, input: null, saved: null, legacy: null, legacy_review: null };
const saved: AIJobResult = { ...empty, status: "succeeded", input: { source_quality: "public_page", criteria: [], sources: { "description.1": "Python <img src=x> required." } },
  saved: { extracted_at: "2026-09-05T12:00:00Z", fields: { description_language: "de/en", description_languages: [],
    states: { requirements: "stated", languages: "not_stated", experience: "not_stated", work_arrangement: "not_stated", responsibilities: "not_stated", seniority: "not_stated", employment_type: "not_stated" },
    requirements: [{ term: "Python", canonical_term: "Python", strength: "required", evidence: [{ source_ref: "description.1", quote: "Python", start: 0, end: 6 }] }],
    languages: [], experience: [], work_arrangement: [], responsibilities: [], seniority: [], employment_type: [], conflicts: [] },
    contract: { model: "gpt-5.4-mini", reasoning: "medium", schema_version: "1" },
    metadata: { model: "gpt-5.4-mini", service_tier: "flex", latency_ms: 100, usage: {} },
    validation: { schema: true, evidence: true, human_reviewed: false } } };

describe("AI extraction pilot", () => {
  beforeEach(() => {
    vi.mocked(fetchAIQueue).mockReset().mockResolvedValue(queue);
    vi.mocked(queueAI).mockReset().mockResolvedValue({ ...queue, added: 10, counts: { queued: 10 }, paused: true });
    vi.mocked(controlAIQueue).mockReset().mockResolvedValue({ ...queue, paused: true });
    vi.mocked(fetchJobExtraction).mockReset().mockResolvedValue(empty);
  });

  it("starts only explicitly and displays the fixed pilot limit", async () => {
    const user = userEvent.setup();
    render(<ExtractionQueue onChanged={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Extract first 10 jobs" })).toBeEnabled();
    expect(queueAI).not.toHaveBeenCalled();
    expect(screen.getByText(/same 10 jobs/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Extract first 10 jobs" }));
    expect(queueAI).toHaveBeenCalledOnce();
    expect(queueAI).toHaveBeenCalledWith(false);
  });

  it("distinguishes document language from requirements and renders evidence as text", async () => {
    vi.mocked(fetchJobExtraction).mockResolvedValue(saved);
    const user = userEvent.setup();
    const { container } = render(<JobExtraction jobId="linkedin:123" />);
    expect(await screen.findByText("de/en")).toBeInTheDocument();
    expect(screen.getByText(/Candidate language requirements/)).toBeInTheDocument();
    await user.click(screen.getByText("Show evidence"));
    expect(container.querySelector("mark")).toHaveTextContent("Python");
    expect(container.querySelector("blockquote")).toHaveTextContent("<img src=x>");
    expect(container.querySelector("img, script")).toBeNull();
    expect(screen.getByText(/Not yet reviewed for accuracy/)).toBeInTheDocument();
  });

  it("preserves old results and review intent without presenting them as verified", async () => {
    vi.mocked(fetchJobExtraction).mockResolvedValue({ ...empty, legacy_review: { status: "interesting", priority: "high" },
      legacy: { model: "old-model", version: 1, created_at: "2025-01-01", fields: { languages: ["German"], summary: "A saved summary" } } });
    const user = userEvent.setup();
    render(<JobExtraction jobId="linkedin:123" />);
    expect(await screen.findByText(/Saved review: interesting · high priority/)).toBeInTheDocument();
    await user.click(screen.getByText("Earlier AI results · Unvalidated"));
    expect(screen.getByText("A saved summary")).toBeVisible();
    expect(screen.getByText(/no individual source quotations/)).toBeVisible();
  });

  it("does not display an old job response after selection changes", async () => {
    let resolve!: (value: AIJobResult) => void;
    vi.mocked(fetchJobExtraction).mockReturnValueOnce(new Promise((done) => { resolve = done; })).mockResolvedValue({ ...empty, job_id: "linkedin:456" });
    const { rerender } = render(<JobExtraction jobId="linkedin:123" />);
    rerender(<JobExtraction jobId="linkedin:456" />);
    await screen.findByText(/No new AI result yet/);
    await act(async () => resolve(saved));
    expect(screen.queryByText("de/en")).not.toBeInTheDocument();
  });
});
