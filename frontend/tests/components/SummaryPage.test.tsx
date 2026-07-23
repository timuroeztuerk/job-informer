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
});
