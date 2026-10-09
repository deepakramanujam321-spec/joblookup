import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ConfirmProvider, ToastProvider } from "../components/ui";

type Handler = (url: string, init?: RequestInit) => unknown;

/** Routes fetch() to handlers keyed by "METHOD /path" (query string ignored). */
export function mockApi(routes: Record<string, Handler | unknown>) {
  const calls: { method: string; url: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.split("?")[0];
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const key = Object.keys(routes).find((k) => {
      const [m, p] = k.split(" ");
      return m === method && new RegExp(`^${p.replace(/:\w+/g, "[^/]+")}$`).test(path);
    });
    if (!key) return new Response(JSON.stringify({ detail: `no mock for ${method} ${path}` }), { status: 404 });
    const handler = routes[key];
    const data = typeof handler === "function" ? (handler as Handler)(url, init) : handler;
    if (data instanceof Response) return data;
    return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export function renderAt(path: string, routePattern: string, element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ToastProvider>
          <ConfirmProvider>
            <Routes><Route path={routePattern} element={element} /></Routes>
          </ConfirmProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
