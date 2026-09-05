import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExtractionQueue from "../../src/components/ExtractionQueue";
import JobExtraction from "../../src/components/JobExtraction";
import { controlAIQueue, fetchAIQueue, fetchJobExtraction, queueAI } from "../../src/api";
import type { AIJobResult, AIQueueState } from "../../src/types";

vi.mock("../../src/api", () => ({ fetchAIQueue: vi.fn(), fetchJobExtraction: vi.fn(), queueAI: vi.fn(), controlAIQueue: vi.fn() }));

const queue: AIQueueState = { batch_size: 100, configured: true, paused: false, reason: null, cooldown_until: 0,
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

describe("AI extraction", () => {
  beforeEach(() => {
    vi.mocked(fetchAIQueue).mockReset().mockResolvedValue(queue);
    vi.mocked(queueAI).mockReset().mockResolvedValue({ ...queue, added: 10, counts: { queued: 10 }, paused: true });
    vi.mocked(controlAIQueue).mockReset().mockResolvedValue({ ...queue, paused: true });
    vi.mocked(fetchJobExtraction).mockReset().mockResolvedValue(empty);
  });

  it("offers the next batch of 100 jobs and starts only explicitly", async () => {
    const user = userEvent.setup();
    const onActivityChange = vi.fn();
    render(<ExtractionQueue onChanged={vi.fn()} onActivityChange={onActivityChange} />);
    expect(await screen.findByRole("button", { name: "Extract next 100" })).toBeEnabled();
    expect(queueAI).not.toHaveBeenCalled();
    expect(screen.getByText(/Each click queues up to 100/)).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "Extract next 100" }));
    expect(queueAI).toHaveBeenCalledOnce();
    expect(queueAI).toHaveBeenCalledWith(false);
    expect(onActivityChange).toHaveBeenCalledWith("AI paused");
  });

  it("keeps pause and resume visible while recent jobs, retries and usage are optional", async () => {
    const user = userEvent.setup();
    let current: AIQueueState = { ...queue, counts: { succeeded: 2, running: 1, queued: 4, failed: 1 },
      jobs: [{ job_id: "linkedin:123", title: "Data Scientist", company: "Acme", status: "succeeded" }],
      usage: { input_tokens: 1000, output_tokens: 2000, reasoning_tokens: 1500, cached_tokens: 0 }, estimated_cost_usd: 0.005 };
    vi.mocked(fetchAIQueue).mockImplementation(async () => current);
    vi.mocked(controlAIQueue).mockImplementation(async (action) => {
      current = { ...current, paused: action === "pause", reason: "Paused by you." };
      return current;
    });
    render(<ExtractionQueue onChanged={vi.fn()} />);
    expect(await screen.findByRole("button", { name: "Pause AI extraction" })).toBeVisible();
    expect(screen.getByText("2 ready · 1 processing · 4 queued · 1 need attention")).toBeVisible();
    expect(screen.getByText("Data Scientist")).not.toBeVisible();
    expect(screen.getByText("Estimated cost")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "Pause AI extraction" }));
    expect(await screen.findByRole("button", { name: "Resume AI extraction" })).toBeVisible();
    expect(screen.getByText("Paused by you.")).toBeVisible();
    expect(screen.getByText("Retry next 100 failed")).not.toBeVisible();
    await user.click(screen.getByText("Results & options"));
    expect(screen.getByRole("link", { name: "Data Scientist" })).toHaveAttribute("href", "/?view=dashboard&job=linkedin%3A123");
    expect(screen.getByText("$0.0050")).toBeVisible();
    expect(screen.getByText(/Reasoning is included in output tokens/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Retry next 100 failed" }));
    expect(queueAI).toHaveBeenCalledWith(true);
    await user.click(screen.getByText("Results & options"));
    await user.click(screen.getByRole("button", { name: "Resume AI extraction" }));
    expect(controlAIQueue).toHaveBeenLastCalledWith("resume");
  });

  it("distinguishes document language from requirements and renders evidence as text", async () => {
    vi.mocked(fetchJobExtraction).mockResolvedValue(saved);
    const user = userEvent.setup();
    const { container } = render(<JobExtraction jobId="linkedin:123" />);
    expect(await screen.findByText("de/en")).toBeInTheDocument();
    expect(screen.getByText(/Candidate language requirements/)).toBeInTheDocument();
    await user.click(screen.getByText("requirements"));
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

  it("shows education, conditional thresholds, remote possibility and compound alternatives accurately", async () => {
    const result = structuredClone(saved);
    const fields = result.saved!.fields;
    result.saved!.contract.schema_version = "2";
    fields.education = [{ qualification: "Bachelor’s degree", level: "bachelor", fields_of_study: ["Computer Science"], strength: "required", evidence: [] }];
    fields.experience = [
      { wording: "At least 1–2 years", years: { kind: "ambiguous_minimum", lower: 1, upper: 2 }, condition: "for India", evidence: [] },
      { wording: "Ideally 2–4 years", years: { kind: "target_range", lower: 2, upper: 4 }, strength: "preferred", evidence: [] },
      { wording: "5+ years", years: { kind: "minimum", lower: 5, upper: null }, evidence: [] },
    ];
    fields.work_arrangement = [{ wording: "Mobile working", mode: "remote_possible", evidence: [] }];
    fields.requirements = [
      { term: "Palantir", alternative_group: "tools", alternative_option: "A", evidence: [] },
      { term: "MSS", alternative_group: "tools", alternative_option: "A", evidence: [] },
      { term: "Onebrief", alternative_group: "tools", alternative_option: "B", evidence: [] },
    ];
    vi.mocked(fetchJobExtraction).mockResolvedValue(result);
    render(<JobExtraction jobId="linkedin:123" />);
    expect(await screen.findByText("Bachelor’s degree")).toBeInTheDocument();
    expect(screen.getByText(/Minimum threshold stated as 1–2 years; ambiguous/)).toHaveTextContent("Applies: for India");
    expect(screen.getByText(/Target range 2–4 years; not an eligibility ceiling/)).toBeInTheDocument();
    expect(screen.getByText("At least 5 years")).toBeInTheDocument();
    expect(screen.queryByText(/Maximum [24] years/)).not.toBeInTheDocument();
    expect(screen.getByText("remote possible")).toBeInTheDocument();
    expect(screen.getByText("Together with: MSS")).toBeInTheDocument();
    expect(screen.getByText("Alternative to: Palantir + MSS")).toBeInTheDocument();
    expect(screen.queryByText(/no dedicated education/)).not.toBeInTheDocument();
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
