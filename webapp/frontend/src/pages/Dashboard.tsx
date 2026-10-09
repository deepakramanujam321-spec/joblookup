import { Link } from "react-router-dom";
import { useJobs, useOverview, usePipeline } from "../api/hooks";
import { Badge, EmptyState, ErrorState, SalaryBadge, Score, Skeleton, Time } from "../components/ui";
import { postedLabel, title } from "../lib/format";

export function DashboardPage() {
  const overview = useOverview();
  const top = useJobs({ view: "recommended", sort: "priority", page: 1, page_size: 6 });
  const pipeline = usePipeline();
  const o = overview.data;
  const stats = o ? [
    { label: "New this week", value: o.new_this_week, to: "/jobs?view=recent&sort=discovered" },
    { label: "Worth reviewing", value: o.worth_reviewing, to: "/jobs?view=needs_review" },
    { label: "High priority", value: o.high_priority, to: "/jobs?view=recommended&sort=priority" },
    { label: "In progress", value: o.in_progress, to: "/applications" },
    { label: "Interviews scheduled", value: o.interviews_scheduled, to: "/applications" },
    { label: "Awaiting your feedback", value: o.awaiting_feedback, to: "/jobs?view=dismissed", hint: "Dismissed or closed jobs you haven't said why about" },
  ] : [];
  const run = o?.last_collect;
  const failures = run?.source_stats ? Object.entries(run.source_stats).filter(([, s]) => s.error) : [];

  return (
    <>
      <div className="page-head">
        <div><h1>Overview</h1><p>What deserves your attention right now.</p></div>
        <Link className="btn primary" to="/jobs?view=needs_review">Review jobs</Link>
      </div>
      {overview.error && <ErrorState error={overview.error} onRetry={overview.refetch} />}
      <div className="stats" style={{ marginBottom: 20 }}>
        {overview.isLoading
          ? Array.from({ length: 6 }, (_, i) => <Skeleton key={i} height={72} />)
          : stats.map((s) => (
            <Link key={s.label} to={s.to} className="stat" title={s.hint}>
              <span className="stat-value">{s.value}</span>
              <span className="stat-label">{s.label}</span>
            </Link>
          ))}
      </div>

      <div className="two-col">
        <section className="card" aria-label="Top opportunities">
          <div className="card-head"><h2>Top opportunities</h2><Link to="/jobs" className="small">All jobs →</Link></div>
          {top.isLoading && <div className="card-pad stack">{Array.from({ length: 4 }, (_, i) => <Skeleton key={i} height={44} />)}</div>}
          {top.error && <div className="card-pad"><ErrorState error={top.error} onRetry={top.refetch} /></div>}
          {top.data && top.data.items.length === 0 && <EmptyState title="Nothing to review">New matches show up after the next collection run.</EmptyState>}
          <ul className="job-list">
            {top.data?.items.map((j) => (
              <li key={j.id}>
                <Link to={`/jobs/${j.id}`} className="job-row">
                  <Score value={j.priority_score ?? j.fit_score} />
                  <div className="grow">
                    <div className="job-row-title ellipsis">{j.title}</div>
                    <div className="job-row-meta ellipsis">{[j.company, j.location].filter(Boolean).join(" · ")} · {postedLabel(j.posted_at_ts)}</div>
                    {j.summary && <div className="job-row-summary">{j.summary}</div>}
                    <div className="row wrap" style={{ marginTop: 6, gap: 4 }}><SalaryBadge job={j} /></div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <div className="stack">
          <section className="card card-pad stack tight" aria-label="Latest collection">
            <div className="row between">
              <h2>Latest collection</h2>
              {pipeline.data && <Badge tone={pipeline.data.state === "healthy" ? "good" : pipeline.data.state === "degraded" ? "warn" : pipeline.data.state === "unknown" ? "" : "bad"}>{title(pipeline.data.state)}</Badge>}
            </div>
            {run ? (
              <>
                <dl className="kv">
                  <dt>Finished</dt><dd><Time iso={run.finished_at ?? run.started_at} /></dd>
                  <dt>Discovered</dt><dd>{run.jobs_found ?? 0} listings</dd>
                  <dt>New</dt><dd>{run.jobs_new ?? 0}</dd>
                  <dt>Duplicates removed</dt><dd>{run.jobs_duplicate ?? 0}</dd>
                  <dt>Filtered / rejected</dt><dd>{run.jobs_rejected ?? 0}</dd>
                </dl>
                {failures.length > 0 && <div className="notice warn small">Failed sources: {failures.map(([name, s]) => `${name} (${s.error})`).join("; ")}</div>}
                {run.error && <div className="notice bad small">{run.error}</div>}
              </>
            ) : <p className="muted">No collection run recorded yet.</p>}
            {pipeline.data && run && <p className="small muted">{pipeline.data.message}</p>}
            <Link to="/pipeline" className="small">Pipeline health →</Link>
          </section>

          <section className="card card-pad stack tight" aria-label="Upcoming">
            <h2>Upcoming</h2>
            {o && o.upcoming_tasks.length === 0 && <p className="muted small">No interviews or reminders scheduled.</p>}
            {o?.upcoming_tasks.map((t) => (
              <Link key={t.id} to={`/jobs/${t.job_id}`} className="row between" style={{ color: "inherit" }}>
                <span className="grow ellipsis"><Badge>{title(t.kind)}</Badge> {t.title} · <span className="muted">{t.company}</span></span>
                <span className="small muted">{t.due_at ? <Time iso={t.due_at} /> : "No date"}</span>
              </Link>
            ))}
          </section>
        </div>
      </div>
    </>
  );
}
