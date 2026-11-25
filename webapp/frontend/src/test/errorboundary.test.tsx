import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { mockApi } from "./utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

function Bomb({ explode }: { explode: boolean }) {
  if (explode) throw new Error("kaboom");
  return <p>all good</p>;
}

describe("ErrorBoundary", () => {
  beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
  afterEach(() => vi.restoreAllMocks());

  it("shows the error with reload and back-to-dashboard actions instead of a blank page", async () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(<ErrorBoundary><Bomb explode /></ErrorBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong on this page");
    expect(screen.getByText("kaboom")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to dashboard" })).toHaveAttribute("href", "/");
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalled();
  });

  it("recovers when the reset key changes (navigation)", () => {
    const { rerender } = render(<ErrorBoundary resetKey="/a"><Bomb explode /></ErrorBoundary>);
    expect(screen.getByText("kaboom")).toBeInTheDocument();
    rerender(<ErrorBoundary resetKey="/b"><Bomb explode={false} /></ErrorBoundary>);
    expect(screen.getByText("all good")).toBeInTheDocument();
  });

  it("keeps the app shell when a routed page throws", async () => {
    // a profile payload with no fields at all makes ProfilePage throw while rendering
    mockApi({ "GET /api/v2/overview": { worth_reviewing: 0, in_progress: 0 }, "GET /api/v2/profile": { data: {}, version: 1, updated_at: null, additional_info: "" } });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/profile"]}><App /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Something went wrong on this page")).toBeInTheDocument();
    expect(screen.getAllByRole("navigation", { name: "Main" }).length).toBeGreaterThan(0);
  });
});
