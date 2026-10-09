import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useFacets, useJobs } from "../api/hooks";
import type { JobListItem } from "../api/types";
import { Badge, EmptyState, ErrorState, SalaryBadge, Score, Skeleton } from "../components/ui";
import { JobDetailView } from "../components/JobDetail";
import { FRESHNESS_LABELS, SOURCE_LABELS, STATUS_LABELS, listFreshness, postedLabel, relative, title } from "../lib/format";
import { activeFilterCount, fromSearchParams, remember, toSearchParams, type JobQuery } from "../lib/filters";

const VIEWS: { value: string; label: string }[] = [
  { value: "recommended", label: "Recommended" },
  { value: "needs_review", label: "Needs review" },
  { value: "saved", label: "Saved" },
  { value: "recent", label: "New" },
  { value: "applied", label: "Applied" },
  { value: "stale", label: "Stale" },
  { value: "dismissed", label: "Dismissed" },
  { value: "all", label: "All" },
];
const SORTS = [
  { value: "priority", label: "Best match (priority)" },
  { value: "fit", label: "Fit score" },
  { value: "newest", label: "Newest posting" },
  { value: "discovered", label: "Recently discovered" },
  { value: "deadline", label: "Closing date" },
  { value: "company", label: "Company A–Z" },
];

export function JobsPage() {
  const { jobId } = useParams();
  const selectedId = jobId ? Number(jobId) : null;
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => fromSearchParams(params), [params]);
  const navigate = useNavigate();
  const jobs = useJobs(query);

  useEffect(() => remember(query), [query]);

  const update = (patch: Partial<JobQuery>, resetPage = true) => {
    const next = { ...query, ...patch, ...(resetPage ? { page: 1 } : {}) };
    setParams(toSearchParams(next), { replace: true });
  };
  const search = params.toString();
  const open = (id: number) => navigate({ pathname: `/jobs/${id}`, search: search ? `?${search}` : "" });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Jobs</h1>
          <p>{jobs.data ? `${jobs.data.total} ${jobs.data.total === 1 ? "job" : "jobs"} in this view` : "Loading…"}</p>
        </div>
      </div>
      <div className={`workspace${selectedId ? " has-selection" : ""}`}>
        <section className="card list-pane" aria-label="Job list">
          <Toolbar query={query} update={update} />
          <JobList
            items={jobs.data?.items}
            loading={jobs.isLoading}
            error={jobs.error}
            refetch={jobs.refetch}
            selectedId={selectedId}
            onOpen={open}
            view={query.view}
          />
          {jobs.data && jobs.data.total > query.page_size && (
            <Pager page={query.page} pageSize={query.page_size} total={jobs.data.total} fetching={jobs.isFetching}
              onPage={(page) => update({ page }, false)} />
          )}
        </section>
        <section className="detail-pane" aria-label="Job details">
          {selectedId ? (
            <>
              <Link className="btn ghost back-link" to={{ pathname: "/jobs", search: search ? `?${search}` : "" }}>← Back to list</Link>
              <JobDetailView jobId={selectedId} />
            </>
          ) : (
            <div className="card"><EmptyState title="Select a job">Pick a job from the list to see its full description, match breakdown and application workspace. Use ↑/↓ to move through the list.</EmptyState></div>
          )}
        </section>
      </div>
    </>
  );
}

function Toolbar({ query, update }: { query: JobQuery; update: (p: Partial<JobQuery>) => void }) {
  const [text, setText] = useState(query.q ?? "");
  const [showFilters, setShowFilters] = useState(activeFilterCount(query) > 0);
  const facets = useFacets();
  const timer = useRef<number>();
  useEffect(() => setText(query.q ?? ""), [query.q]);
  const onSearch = (value: string) => {
    setText(value);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => update({ q: value || undefined }), 300);
  };
  const count = activeFilterCount(query);
  return (
    <div className="list-toolbar">
      <label className="sr-only" htmlFor="job-search">Search jobs</label>
      <input id="job-search" className="input" type="search" placeholder="Search title, company, skills, description…" value={text}
        onChange={(e) => onSearch(e.target.value)} />
      <div className="segmented" role="tablist" aria-label="Views">
        {VIEWS.map((v) => (
          <button key={v.value} role="tab" aria-selected={query.view === v.value} onClick={() => update({ view: v.value })}>{v.label}</button>
        ))}
      </div>
      <div className="row">
        <label className="sr-only" htmlFor="job-sort">Sort by</label>
        <select id="job-sort" className="select grow" value={query.sort} onChange={(e) => update({ sort: e.target.value })}>
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <button className="btn" aria-expanded={showFilters} onClick={() => setShowFilters((s) => !s)}>
          Filters{count > 0 ? ` (${count})` : ""}
        </button>
      </div>
      {showFilters && (
        <div className="filters-panel">
          <div className="field">
            <label htmlFor="f-score">Min fit score</label>
            <select id="f-score" className="select" value={query.min_score ?? ""} onChange={(e) => update({ min_score: e.target.value ? Number(e.target.value) : undefined })}>
              <option value="">Any</option>
              {[50, 60, 70, 80].map((s) => <option key={s} value={s}>{s}+</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-posted">Posted within</label>
            <select id="f-posted" className="select" value={query.posted_within_days ?? ""} onChange={(e) => update({ posted_within_days: e.target.value ? Number(e.target.value) : undefined })}>
              <option value="">Any time (incl. unknown)</option>
              <option value="3">3 days</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option>
            </select>
          </div>
          <FacetSelect id="f-remote" label="Work mode" value={query.remote_type} options={facets.data?.remote_type} onChange={(v) => update({ remote_type: v })} />
          <FacetSelect id="f-seniority" label="Seniority" value={query.seniority} options={facets.data?.seniority} onChange={(v) => update({ seniority: v })} />
          <FacetSelect id="f-type" label="Employment type" value={query.employment_type} options={facets.data?.employment_type} onChange={(v) => update({ employment_type: v })} />
          <FacetSelect id="f-source" label="Source" value={query.source} options={facets.data?.sources} labels={SOURCE_LABELS} onChange={(v) => update({ source: v })} />
          <FacetSelect id="f-status" label="Application status" value={query.status} options={facets.data?.statuses} labels={STATUS_LABELS} onChange={(v) => update({ status: v })} />
          <div className="field">
            <label htmlFor="f-location">Location contains</label>
            <input id="f-location" className="input" defaultValue={query.location ?? ""} placeholder="e.g. Bengaluru"
              onBlur={(e) => update({ location: e.target.value.trim() || undefined })}
              onKeyDown={(e) => e.key === "Enter" && update({ location: (e.target as HTMLInputElement).value.trim() || undefined })} />
          </div>
          {count > 0 && (
            <button className="btn ghost sm" style={{ gridColumn: "1 / -1", justifySelf: "start" }}
              onClick={() => update({ min_score: undefined, posted_within_days: undefined, remote_type: undefined, seniority: undefined, employment_type: undefined, source: undefined, status: undefined, location: undefined })}>
              Clear filters
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function FacetSelect({ id, label, value, options, labels, onChange }: { id: string; label: string; value?: string; options?: string[]; labels?: Record<string, string>; onChange: (v?: string) => void }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="select" value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">Any</option>
        {(options ?? []).map((o) => <option key={o} value={o}>{labels?.[o] ?? title(o)}</option>)}
      </select>
    </div>
  );
}

const EMPTY_COPY: Record<string, string> = {
  saved: "Jobs you save appear here.",
  applied: "Jobs you mark as applied appear here.",
  stale: "Nothing looks stale: every listing has been confirmed open recently.",
  needs_review: "You're caught up. New strong matches land here after each collection run.",
  dismissed: "Dismissed and rejected jobs appear here.",
};

export function JobList({ items, loading, error, refetch, selectedId, onOpen, view }: {
  items?: JobListItem[]; loading: boolean; error: unknown; refetch: () => void; selectedId: number | null; onOpen: (id: number) => void; view: string;
}) {
  const listRef = useRef<HTMLUListElement>(null);
  if (loading) {
    return <div className="stack" style={{ padding: 12 }}>{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} height={54} />)}</div>;
  }
  if (error) return <div style={{ padding: 12 }}><ErrorState error={error} onRetry={refetch} /></div>;
  if (!items?.length) return <EmptyState title="No jobs here">{EMPTY_COPY[view] ?? "Try another view or clear some filters."}</EmptyState>;

  const onKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    if (!["ArrowDown", "ArrowUp", "j", "k"].includes(e.key)) return;
    e.preventDefault();
    const idx = items.findIndex((i) => i.id === selectedId);
    const nextIdx = e.key === "ArrowDown" || e.key === "j" ? Math.min(items.length - 1, idx + 1) : Math.max(0, idx - 1);
    const next = items[nextIdx];
    if (next) {
      onOpen(next.id);
      listRef.current?.querySelector<HTMLElement>(`[data-id="${next.id}"]`)?.focus();
    }
  };

  return (
    <ul className="job-list" ref={listRef} onKeyDown={onKeyDown} aria-label="Jobs">
      {items.map((job) => <JobRow key={job.id} job={job} selected={job.id === selectedId} onOpen={onOpen} />)}
    </ul>
  );
}

function JobRow({ job, selected, onOpen }: { job: JobListItem; selected: boolean; onOpen: (id: number) => void }) {
  const freshness = listFreshness(job);
  return (
    <li>
      <a href={`/jobs/${job.id}`} data-id={job.id} className="job-row" aria-current={selected}
        onClick={(e) => { e.preventDefault(); onOpen(job.id); }}>
        <Score value={job.priority_score ?? job.fit_score} label={`Priority ${job.priority_score ?? "—"} · Fit ${job.fit_score ?? "—"}`} />
        <div className="grow">
          <div className="row between">
            <span className="job-row-title ellipsis">{job.title}</span>
            {job.saved && <span aria-label="Saved" title="Saved">★</span>}
          </div>
          <div className="job-row-meta ellipsis">
            {[job.company || "Company not stated", job.location || (job.remote_type ? title(job.remote_type) : null)].filter(Boolean).join(" · ")}
          </div>
          {job.summary && <div className="job-row-summary">{job.summary}</div>}
          <div className="row wrap" style={{ marginTop: 6, gap: 4 }}>
            <SalaryBadge job={job} />
            <Badge tone={job.posted_at_ts ? "" : "warn"} title={job.posted_at_ts ? undefined : `Discovered ${relative(job.discovered_at)}`}>
              {postedLabel(job.posted_at_ts)}
            </Badge>
            {freshness !== "verified" && freshness !== "unchecked" && (
              <Badge tone={freshness === "closed" || freshness === "not_a_listing" ? "bad" : "warn"}>{FRESHNESS_LABELS[freshness]}</Badge>
            )}
            {!["discovered", "needs_review"].includes(job.workflow_status) && <Badge tone="accent">{STATUS_LABELS[job.workflow_status]}</Badge>}
          </div>
        </div>
      </a>
    </li>
  );
}

function Pager({ page, pageSize, total, fetching, onPage }: { page: number; pageSize: number; total: number; fetching: boolean; onPage: (p: number) => void }) {
  const pages = Math.ceil(total / pageSize);
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav className="pager" aria-label="Pagination">
      <span>{from}–{to} of {total}{fetching ? " · loading…" : ""}</span>
      <span className="row">
        <button className="btn sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
        <span>Page {page} / {pages}</span>
        <button className="btn sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
      </span>
    </nav>
  );
}
