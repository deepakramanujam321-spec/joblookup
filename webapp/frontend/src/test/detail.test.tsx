import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobDetailView } from "../components/JobDetail";
import { detail } from "./fixtures";
import { mockApi, renderAt } from "./utils";

const categories = [{ value: "excellent_match", label: "Excellent match", teaches: true }, { value: "wrong_seniority", label: "Wrong seniority", teaches: true }, { value: "inaccurate_listing", label: "Expired or inaccurate listing", teaches: false }];

describe("Job detail", () => {
  it("shows honest dates, match breakdown and unknowns", async () => {
    mockApi({ "GET /api/v2/jobs/1": detail() });
    renderAt("/", "/", <JobDetailView jobId={1} />);
    expect(await screen.findByText("Posting date unavailable")).toBeInTheDocument();
    expect(screen.getByText("Skills alignment")).toBeInTheDocument();
    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    expect(screen.getByTitle("Not enough information to judge")).toHaveTextContent("n/a");
    expect(screen.getByText(/Unknown: no posting date from source/)).toBeInTheDocument();
  });

  it("labels partial descriptions and renders structure", async () => {
    mockApi({ "GET /api/v2/jobs/1": detail({ description_is_partial: true, description_source: "page_text" }) });
    renderAt("/", "/", <JobDetailView jobId={1} />);
    await userEvent.click(await screen.findByRole("tab", { name: "Description" }));
    expect(screen.getByText(/may be a partial description/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Requirements" })).toBeInTheDocument();
  });

  it("submits structured feedback", async () => {
    const calls = mockApi({
      "GET /api/v2/jobs/1": detail(),
      "GET /api/v2/feedback/categories": categories,
      "POST /api/v2/jobs/1/feedback": { newly_learned: [], priority_now: 80 },
    });
    renderAt("/", "/", <JobDetailView jobId={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Feedback" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByLabelText("Wrong seniority"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Submit" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ category: "wrong_seniority" }));
  });

  it("asks for confirmation before marking applied", async () => {
    const calls = mockApi({ "GET /api/v2/jobs/1": detail(), "PUT /api/v2/jobs/1/application": { ...detail(), workflow_status: "applied" } });
    renderAt("/", "/", <JobDetailView jobId={1} />);
    await userEvent.click(await screen.findByRole("button", { name: "Mark applied" }));
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Applied" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ status: "applied" }));
  });

  it("saves edited drafts as new versions and keeps Gmail states distinct", async () => {
    const draft = { id: 9, job_id: 1, version: 1, origin: "generated", parent_id: null, subject: "Hi", body: "Original", cover_letter: null,
      qualifications: [], missing_info: ["Do you have Kubernetes experience?"], answers: [], resume_id: null, model: "m",
      gmail_status: "not_saved", gmail_saved_at: null, gmail_error: null, gmail_open_url: null, created_at: new Date().toISOString() };
    const calls = mockApi({
      "GET /api/v2/jobs/1": detail(),
      "GET /api/v2/jobs/1/drafts": { items: [draft], llm_configured: true },
      "POST /api/v2/jobs/1/drafts": { ...draft, id: 10, version: 2, origin: "edited", body: "Edited" },
      "GET /api/v2/resumes": { items: [], max_bytes: 5242880, llm_configured: true },
      "GET /api/v2/integrations": { configured: false, redirect_uri: "", picker_ready: false, services: { gmail: null, drive: null } },
    });
    renderAt("/", "/", <JobDetailView jobId={1} />);
    await userEvent.click(await screen.findByRole("tab", { name: "Application" }));
    const body = await screen.findByLabelText("Email");
    expect(screen.getByText("Not in Gmail")).toBeInTheDocument();
    expect(screen.getByText(/Do you have Kubernetes experience/)).toBeInTheDocument();
    await userEvent.clear(body);
    await userEvent.type(body, "Edited");
    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ body: "Edited", parent_id: 9 }));
    expect(screen.getByRole("link", { name: /Connect Gmail/ })).toBeInTheDocument();
  });
});
