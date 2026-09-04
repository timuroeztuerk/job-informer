import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SummaryPage from "../../src/components/SummaryPage";
import { fetchDbSummary } from "../../src/api";

vi.mock("../../src/api", () => ({
  fetchDbSummary: vi.fn(),
  getApiErrorMessage: (_error: unknown, fallback: string) => `${fallback} Check VITE_API_BASE.`,
}));

describe("Intelligence loading state", () => {
  beforeEach(() => {
    vi.mocked(fetchDbSummary).mockReset().mockRejectedValue(new TypeError("Failed to fetch"));
  });

  it("shows one concise retry state instead of empty intelligence sections", async () => {
    const user = userEvent.setup();
    render(<SummaryPage onOpenDashboard={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load intelligence from the API");
    expect(screen.queryByText("Current database posture")).not.toBeInTheDocument();
    expect(screen.queryByText("Best matches for your profile")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(fetchDbSummary).toHaveBeenCalledTimes(2);
  });

  it("shows only broad current-state intelligence", async () => {
    vi.mocked(fetchDbSummary).mockResolvedValue({
      totals: {
        total_jobs: 12,
        archived_jobs: 4,
        recent_jobs_7_days: 3,
        date_range: { earliest: "2026-07-01T00:00:00Z", latest: "2026-07-12T00:00:00Z" },
      },
      jobs_by_source: { LinkedIn: 12 },
      top_companies: [{ name: "ACME", count: 4, percentage: 33.3 }],
      top_locations: [{ name: "Berlin", count: 6, percentage: 50 }],
      role_families: [{ name: "analytics_bi", count: 7, percentage: 58.3 }],
      query_groups: [{ name: "Data science", count: 8, percentage: 66.7 }],
      collection_freshness: {
        as_of: "2026-07-12T12:00:00Z",
        last_collected_at: "2026-07-12T08:00:00Z",
        latest_observation_at: "2026-07-12T08:00:00Z",
        latest_scrape_at: "2026-07-12T08:00:00Z",
        source: "job_observation",
        age_days: 0.16,
        status: "fresh",
      },
    });

    render(<SummaryPage onOpenDashboard={vi.fn()} />);

    expect(await screen.findByText("Top companies")).toBeInTheDocument();
    expect(screen.getByText("Top locations")).toBeInTheDocument();
    expect(screen.getByText("Target role families")).toBeInTheDocument();
    expect(screen.getByText("Query groups")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.queryByText(/parser/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/momentum/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/fit coverage/i)).not.toBeInTheDocument();
  });
});
