import React, { useRef, useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { archiveJob, fetchJob, fetchJobs, restoreJob, setJobFavorite, setJobFlag } from "../../src/api";
import type { Job, JobsResponse } from "../../src/types";
import JobDetail from "../../src/components/JobDetail";
import JobTable, { type JobTableHandle } from "../../src/components/JobTable";
vi.mock("../../src/components/JobDescription", () => ({ default: () => null }));

vi.mock("../../src/api", () => ({
  archiveJob: vi.fn(),
  fetchJob: vi.fn(),
  fetchJobs: vi.fn(),
  restoreJob: vi.fn(),
  setJobFavorite: vi.fn(),
  setJobFlag: vi.fn(),
}));

const makeJob = (job_id: string, title: string): Job => ({
  job_id,
  title,
  company: `${title} Co`,
  location: "Berlin",
  source: "LinkedIn",
  is_favorite: false,
  salary: "Not specified",
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
        onFlagChanged={(flag) => {
          setSelectedJob((current) => current?.job_id === flag.job_id ? { ...current, ...flag } : current);
          tableRef.current?.reload(false);
        }}
        onArchiveChanged={() => tableRef.current?.reload(false)}
        onFavoriteChanged={(jobId, isFavorite) => {
          setSelectedJob((current) =>
            current?.job_id === jobId ? { ...current, is_favorite: isFavorite } : current
          );
          tableRef.current?.reload(false);
        }}
      />
    </div>
  );
};

describe("JobTable and JobDetail", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/?view=dashboard&status=unreviewed&priority=high");
    vi.mocked(archiveJob).mockReset().mockResolvedValue(undefined);
    vi.mocked(restoreJob).mockReset().mockResolvedValue(undefined);
    vi.mocked(fetchJobs).mockReset();
    vi.mocked(fetchJob).mockReset().mockImplementation(async (jobId) => {
      const match = [firstJob, secondJob].find((job) => job.job_id === jobId);
      if (!match) throw new Error("Unknown job");
      return match;
    });
    vi.mocked(setJobFavorite).mockReset().mockImplementation(async (jobId, isFavorite) => ({
      job_id: jobId,
      is_favorite: isFavorite,
    }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(setJobFlag).mockReset();
  });

  it("persists the flagged filter and advances after removing an example", async () => {
    window.history.replaceState({}, "", "/?view=dashboard&flagged=1&archived=include");
    let items = [firstJob, secondJob].map((job) => ({ ...job, is_flagged: true, flag_reason: "Wrong role" }));
    vi.mocked(fetchJobs).mockImplementation(async () => response(items));
    vi.mocked(setJobFlag).mockImplementation(async (jobId) => {
      items = items.filter((item) => item.job_id !== jobId);
      return { job_id: jobId, is_flagged: false, flag_reason: null, flagged_at: null };
    });
    const user = userEvent.setup();
    render(<JobList />);
    expect(await screen.findByRole("status", { name: "2 flagged all records" })).toBeInTheDocument();
    expect(screen.getByLabelText("Flagged only")).toBeChecked();
    expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({ flagged: true, archived: "include" }));
    expect(screen.getAllByText("Flagged for review")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Unflag" }));
    expect(await screen.findByRole("heading", { name: "Second role" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^First role/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({ flagged: false })));
    expect(new URLSearchParams(window.location.search).has("flagged")).toBe(false);
  });

  it("shows an honest loading label before the first job response", async () => {
    let resolveJobs!: (value: JobsResponse) => void;
    vi.mocked(fetchJobs).mockReturnValue(
      new Promise((resolve) => {
        resolveJobs = resolve;
      })
    );

    render(<JobTable companies={[]} onSelect={vi.fn()} />);

    expect(screen.getByRole("status", { name: "Loading jobs…" })).toBeInTheDocument();
    resolveJobs(response([firstJob]));
    expect(await screen.findByRole("button", { name: /^First role/ })).toBeInTheDocument();
  });

  it("pages through ten jobs at a time and resets when a quick filter changes", async () => {
    const items = Array.from({ length: 21 }, (_, i) => makeJob(`page-job-${i}`, `Role ${i + 1}`));
    vi.mocked(fetchJobs).mockImplementation(async ({ limit = 10, offset = 0 } = {}) => ({
      total: items.length, count: items.slice(offset, offset + limit).length,
      items: items.slice(offset, offset + limit),
    }));
    const user = userEvent.setup();
    render(<JobTable companies={[]} onSelect={vi.fn()} />);

    await screen.findByRole("button", { name: /^Role 1 / });
    expect(screen.getAllByRole("article")).toHaveLength(10);
    expect(screen.getByText("Page 1 of 3")).toBeVisible();
    expect(screen.getByRole("button", { name: "Prev" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("button", { name: /^Role 11 / });
    expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 10, offset: 10 }));
    expect(new URLSearchParams(window.location.search).get("page")).toBe("2");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("button", { name: /^Role 21 / });
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Prev" }));
    await screen.findByRole("button", { name: /^Role 11 / });
    await user.click(screen.getByLabelText("Favorites only"));
    await screen.findByRole("button", { name: /^Role 1 / });
    expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({ favorite: true, offset: 0, limit: 10 }));
    expect(screen.getByRole("button", { name: /^Filters/ })).toHaveAttribute("aria-expanded", "false");
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
    expect(screen.getByRole("button", { name: /^First role/ })).toHaveAttribute("aria-current", "true");
    expect(vi.mocked(fetchJobs).mock.calls[0][0]).not.toHaveProperty("annotationStatus");
    expect(vi.mocked(fetchJobs).mock.calls[0][0]).not.toHaveProperty("annotationPriority");

    await user.click(screen.getByRole("button", { name: /^Second role/ }));
    await within(detail).findByRole("heading", { name: "Second role" });
    expect(screen.getByRole("button", { name: /^Second role/ })).toHaveAttribute("aria-current", "true");
    expect(new URLSearchParams(window.location.search).get("job")).toBe("job-2");

    await user.click(screen.getByRole("button", { name: /^First role/ }));
    await within(detail).findByRole("heading", { name: "First role" });
    await user.click(within(detail).getByRole("button", { name: "Archive" }));

    await waitFor(() => {
      expect(archiveJob).toHaveBeenCalledWith("job-1");
      expect(fetchJobs).toHaveBeenCalledTimes(2);
    });
    await within(detail).findByRole("heading", { name: "Second role" });
    expect(screen.queryByRole("button", { name: /^First role/ })).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("job")).toBe("job-2");
    expect(new URLSearchParams(window.location.search).has("status")).toBe(false);
    expect(new URLSearchParams(window.location.search).has("priority")).toBe(false);
  });

  it("keeps a compact canonical card with its repeat count", async () => {
    const repeatedJob: Job = {
      ...firstJob,
      seen_count: 4,
      first_seen_at: "2026-07-09T08:00:00Z",
      last_seen_at: "2026-07-12T08:00:00Z",
    };
    vi.mocked(fetchJobs).mockResolvedValue(response([repeatedJob]));
    const user = userEvent.setup();

    render(<JobTable companies={[]} onSelect={vi.fn()} />);

    expect(await screen.findByText("Repeated 3 times")).toBeInTheDocument();
    expect(screen.queryByText(/Latest repeat/)).not.toBeInTheDocument();
    expect(screen.queryByText("LinkedIn")).not.toBeInTheDocument();
    expect(screen.getByText("First seen Jul 9, 2026")).toBeInTheDocument();
    expect(screen.queryByText(/not specified/i)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".job-table .row")).toHaveLength(1);

    await user.selectOptions(screen.getByLabelText("Sort"), "seen_count_desc");
    await waitFor(() => {
      expect(fetchJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ sort: "seen_count_desc" })
      );
    });
    expect(new URLSearchParams(window.location.search).get("sort")).toBe("seen_count_desc");
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

    await screen.findByRole("button", { name: /^First role/ });
    await user.click(screen.getByRole("button", { name: "Filters" }));
    expect(screen.queryByLabelText("Source")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Company"), "Acme");
    await user.selectOptions(screen.getByLabelText("Role family"), "analytics_bi");
    await user.selectOptions(screen.getByLabelText("Found via"), "data_science");
    await user.click(screen.getByLabelText("Favorites only"));

    await waitFor(() => {
      expect(fetchJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({
          company: "Acme",
          roleFamily: "analytics_bi",
          queryGroup: "data_science",
          favorite: true,
        })
      );
    });
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.getByRole("button", { name: /^Filters/ })).toHaveAttribute("aria-expanded", "false");
    const params = new URLSearchParams(window.location.search);
    expect(params.get("company")).toBe("Acme");
    expect(params.get("role")).toBe("analytics_bi");
    expect(params.get("query_group")).toBe("data_science");
    expect(params.get("favorite")).toBe("1");
  });

  it("shows the filter scope without opening every control and can clear a drill-down", async () => {
    window.history.replaceState({}, "", "/?company=Acme&role=analytics_bi&seen_since=2026-09-01&repeated=1");
    vi.mocked(fetchJobs).mockResolvedValue(response([firstJob]));
    const user = userEvent.setup();
    render(<JobTable companies={[]} onSelect={vi.fn()} />);
    await screen.findByRole("button", { name: /^First role/ });
    expect(screen.getByRole("button", { name: /^Filters/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("searchbox", { name: "Search" })).toBeVisible();
    expect(screen.getByLabelText("Favorites only")).toBeVisible();
    expect(screen.getByLabelText("Flagged only")).toBeVisible();
    expect(screen.getByText(/Company: Acme · Role: analytics bi/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({ company: "", roleFamily: "", lastSeenFrom: "", repeated: false })));
    expect(new URLSearchParams(window.location.search).has("company")).toBe(false);
  });

  it("toggles a favorite directly from the canonical job list", async () => {
    vi.mocked(fetchJobs).mockResolvedValue(response([firstJob]));
    const user = userEvent.setup();

    render(<JobTable companies={[]} onSelect={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "Add First role to favorites" }));

    expect(setJobFavorite).toHaveBeenCalledWith("job-1", true);
    expect(await screen.findByRole("button", { name: "Remove First role from favorites" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("restores an audit filter from the URL and resets pagination when changing or clearing it", async () => {
    window.history.replaceState({}, "", "/?relevance_outcome=unmatched&page=3&company=Acme");
    vi.mocked(fetchJobs).mockResolvedValue(response([firstJob]));
    const user = userEvent.setup();
    render(<JobTable companies={[]} onSelect={vi.fn()} />);

    await screen.findByRole("button", { name: /^First role/ });
    expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      relevanceOutcome: "unmatched", limit: 10, offset: 20, company: "Acme", archived: "exclude",
    }));
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
    expect(screen.getByLabelText("Relevance")).toHaveValue("unmatched");

    await user.selectOptions(screen.getByLabelText("Relevance"), "auto_archived");
    await waitFor(() => expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      relevanceOutcome: "auto_archived", offset: 0, company: "Acme", archived: "only",
    })));
    expect(screen.getByLabelText("Job set")).toHaveValue("only");
    expect(screen.getByLabelText("Job set")).toBeDisabled();
    expect(new URLSearchParams(window.location.search).get("relevance_outcome")).toBe("auto_archived");
    expect(new URLSearchParams(window.location.search).has("page")).toBe(false);

    await user.selectOptions(screen.getByLabelText("Relevance"), "unmatched");
    await waitFor(() => expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      relevanceOutcome: "unmatched", archived: "exclude",
    })));
    expect(screen.getByLabelText("Job set")).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      relevanceOutcome: undefined, archived: "exclude", company: "", offset: 0,
    })));
    expect(new URLSearchParams(window.location.search).has("relevance_outcome")).toBe(false);
  });

  it("opens the automatic archive audit directly and advances after restoring a job", async () => {
    window.history.replaceState({}, "", "/?relevance_outcome=auto_archived");
    const archivedJob = { ...firstJob, archived_at: "2026-09-04T08:00:00Z" };
    vi.mocked(fetchJobs)
      .mockResolvedValueOnce(response([archivedJob, secondJob]))
      .mockResolvedValue(response([secondJob]));
    const user = userEvent.setup();
    render(<JobList />);

    await screen.findByRole("heading", { name: "First role" });
    expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      relevanceOutcome: "auto_archived", archived: "only",
    }));
    await user.click(screen.getByRole("button", { name: "Restore" }));

    expect(restoreJob).toHaveBeenCalledWith("job-1");
    await screen.findByRole("heading", { name: "Second role" });
    expect(screen.queryByRole("button", { name: /^First role/ })).not.toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("relevance_outcome")).toBe("auto_archived");
  });

  it("ignores invalid relevance values from old or edited URLs", async () => {
    window.history.replaceState({}, "", "/?relevance_outcome=unknown");
    vi.mocked(fetchJobs).mockResolvedValue(response([firstJob]));
    render(<JobTable companies={[]} onSelect={vi.fn()} />);

    await screen.findByRole("button", { name: /^First role/ });
    expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({ relevanceOutcome: undefined }));
    expect(new URLSearchParams(window.location.search).has("relevance_outcome")).toBe(false);
  });

  it("preserves Intelligence scope and exact matching until those filters are edited or cleared", async () => {
    window.history.replaceState({}, "", "/?company=Acme&company_exact=1&location=M%C3%BCnchen&location_primary=1&seen_since=2026-08-29T12%3A00%3A00Z&repeated=1&from=2026-08-29T12%3A00%3A00Z");
    vi.mocked(fetchJobs).mockResolvedValue(response([firstJob]));
    const user = userEvent.setup();
    render(<JobTable companies={[]} onSelect={vi.fn()} />);
    await screen.findByRole("button", { name: /^First role/ });
    expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      company: "Acme", companyExact: true, location: "München", locationPrimary: true,
      lastSeenFrom: "2026-08-29T12:00:00Z", repeated: true, dateFrom: "2026-08-29T12:00:00Z",
    }));
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
    expect(screen.getByLabelText("Last seen since")).toHaveValue("2026-08-29");
    expect(screen.getByLabelText("From")).toHaveValue("2026-08-29");
    expect(screen.getByLabelText("Seen more than once")).toBeChecked();
    await user.type(screen.getByLabelText("Company"), " tools");
    await user.type(screen.getByLabelText("Location"), " city");
    await waitFor(() => expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({ companyExact: false, locationPrimary: false })));
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(fetchJobs).toHaveBeenLastCalledWith(expect.objectContaining({
      lastSeenFrom: "", repeated: false, companyExact: false, locationPrimary: false,
    })));
    const params = new URLSearchParams(window.location.search);
    for (const key of ["company_exact", "location_primary", "seen_since", "repeated", "from"]) expect(params.has(key)).toBe(false);
  });
});
