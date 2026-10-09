// One place for HTTP: JSON in/out, consistent error objects, and the
// browser's own Basic-auth prompt (the server sends WWW-Authenticate).

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function messageFrom(detail: unknown, fallback: string): { message: string; code?: string } {
  if (typeof detail === "string") return { message: detail };
  if (detail && typeof detail === "object") {
    const d = detail as { message?: string; code?: string };
    if (d.message) return { message: d.message, code: d.code };
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: string; loc?: (string | number)[] } | undefined;
      if (first?.msg) return { message: `${first.loc?.slice(1).join(".") ?? "input"}: ${first.msg}` };
    }
  }
  return { message: fallback };
}

export async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const isForm = body instanceof FormData;
  let resp: Response;
  try {
    resp = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body !== undefined && !isForm ? { "Content-Type": "application/json" } : undefined,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      ...init,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. If it was asleep, it may take ~30s to wake up — try again.");
  }
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  const data = text ? safeJson(text) : undefined;
  if (!resp.ok) {
    const { message, code } = messageFrom((data as { detail?: unknown })?.detail, `Request failed (${resp.status})`);
    throw new ApiError(resp.status, message, code);
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: <T = void>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
};

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}
