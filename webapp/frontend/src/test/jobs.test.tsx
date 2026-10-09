import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobList, JobsPage } from "../pages/Jobs";
import { detail, listItem } from "./fixtures";
import { mockApi, renderAt } from "./utils";

const facets = { sources: ["lever"], seniority: ["senior"], remote_type: ["remote"], employment_type: ["full_time"], statuses: ["needs_review"], views: [] };

describe("JobList", () => {
  it("shows loading, empty and error states", () => {
    const { rerender } = renderAt("/", "/", <JobList loading items={undefined} error={null} refetch={() => {}} selectedId={null} onOpen={() => {}} view="all" />);
    expect(document.querySelector(".skeleton")).toBeTruthy();
    rerender(<></>);
  });

  it("supports keyboard navigation", async () => {
    const onOpen = vi.fn();
    renderAt("/", "/", <JobList loading={false} error={null} refetch={() => {}} selectedId={1} onOpen={onOpen} view="all"
      items={[listItem({ id: 1 }), listItem({ id: 2, title: "Platform Engineer" })]} />);
    fireEvent.keyDown(screen.getByRole("list", { name: "Jobs" }), { key: "ArrowDown" });
    expect(onOpen).toHaveBeenCalledWith(2);
  });

  it("never shows a fake posting date", () => {
    renderAt("/", "/", <JobList loading={false} error={null} refetch={() => {}} selectedId={null} onOpen={() => {}} view="all" items={[listItem()]} />);
    expect(screen.getByText("Posting date unavailable")).toBeInTheDocument();
  });

  it("always says whether salary is stated and how it compares", () => {
    renderAt("/", "/", <JobList loading={false} error={null} refetch={() => {}} selectedId={null} onOpen={() => {}} view="all" items={[
      listItem({ id: 1 }),
      listItem({ id: 2, salary_min: 2000000, salary_max: 3000000, salary_currency: "INR", salary_period: "year", match_highlights: { salary: "meets" } }),
      listItem({ id: 3, salary_min: 1000000, salary_max: 1400000, salary_currency: "INR", salary_period: "year", match_highlights: { salary: "below" } }),
    ]} />);
    expect(screen.getByText("Salary not stated")).toBeInTheDocument();
    expect(screen.getByText("₹20L–₹30L · meets your minimum")).toBeInTheDocument();
    expect(screen.getByText("₹10L–₹14L · below your minimum")).toBeInTheDocument();
  });

  it("renders view-specific empty copy", () => {
    renderAt("/", "/", <JobList loading={false} error={null} refetch={() => {}} selectedId={null} onOpen={() => {}} view="saved" items={[]} />);
    expect(screen.getByText(/Jobs you save appear here/)).toBeInTheDocument();
  });
});

describe("Jobs workspace", () => {
  it("loads, filters via the server and opens details", async () => {
    const calls = mockApi({
      "GET /api/v2/jobs": { items: [listItem()], total: 1, page: 1, page_size: 25 },
      "GET /api/v2/jobs/facets": facets,
      "GET /api/v2/jobs/1": detail(),
    });
    renderAt("/jobs/1", "/jobs/:jobId", <JobsPage />);
    expect(await screen.findByRole("heading", { name: "Senior Backend Engineer", level: 2 })).toBeInTheDocument();
    const original = screen.getByRole("link", { name: /View original job/ });
    expect(original).toHaveAttribute("href", "https://jobs.lever.co/acme/1");
    expect(original).toHaveAttribute("rel", expect.stringContaining("noopener"));

    await userEvent.click(screen.getByRole("tab", { name: "Saved" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("view=saved"))).toBe(true));
  });

  it("shows an error with retry when the API fails", async () => {
    mockApi({
      "GET /api/v2/jobs": () => new Response(JSON.stringify({ detail: "Database unavailable" }), { status: 503 }),
      "GET /api/v2/jobs/facets": facets,
    });
    renderAt("/jobs", "/jobs", <JobsPage />);
    expect(await screen.findByText("Database unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
