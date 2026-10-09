// Job-list query state lives in the URL (shareable, back-button friendly);
// the last-used filters are remembered locally as the default next time.

export interface JobQuery {
  view: string;
  q?: string;
  status?: string;
  min_score?: number;
  seniority?: string;
  remote_type?: string;
  employment_type?: string;
  location?: string;
  source?: string;
  posted_within_days?: number;
  sort: string;
  page: number;
  page_size: number;
}

const STORAGE_KEY = "joblookup.jobFilters";
const NUMERIC = new Set(["min_score", "posted_within_days", "page", "page_size"]);
export const DEFAULT_QUERY: JobQuery = { view: "recommended", sort: "priority", page: 1, page_size: 25 };
const FIELDS: (keyof JobQuery)[] = ["view", "q", "status", "min_score", "seniority", "remote_type", "employment_type", "location", "source", "posted_within_days", "sort", "page", "page_size"];

export function fromSearchParams(params: URLSearchParams): JobQuery {
  const hasAny = FIELDS.some((f) => params.has(f));
  const base = hasAny ? { ...DEFAULT_QUERY } : loadSaved();
  for (const f of FIELDS) {
    const raw = params.get(f);
    if (raw === null || raw === "") continue;
    (base as unknown as Record<string, unknown>)[f] = NUMERIC.has(f) ? Number(raw) : raw;
  }
  return base;
}

export function toSearchParams(q: JobQuery): URLSearchParams {
  const params = new URLSearchParams();
  for (const f of FIELDS) {
    const v = q[f];
    if (v === undefined || v === null || v === "") continue;
    if (f !== "view" && f !== "sort" && DEFAULT_QUERY[f] === v) continue;
    params.set(f, String(v));
  }
  return params;
}

export function remember(q: JobQuery): void {
  try {
    const { page: _page, q: _q, ...persistent } = q;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persistent));
  } catch {
    /* storage unavailable (private mode): filters just won't persist */
  }
}

function loadSaved(): JobQuery {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_QUERY, ...JSON.parse(raw), page: 1 };
  } catch {
    /* ignore corrupt or unavailable storage */
  }
  return { ...DEFAULT_QUERY };
}

export function activeFilterCount(q: JobQuery): number {
  return (["status", "min_score", "seniority", "remote_type", "employment_type", "location", "source", "posted_within_days"] as const)
    .filter((k) => q[k] !== undefined && q[k] !== "").length;
}
