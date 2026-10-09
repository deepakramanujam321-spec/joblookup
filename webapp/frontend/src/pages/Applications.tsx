import { useState } from "react";
import { Link } from "react-router-dom";
import { useApplications } from "../api/hooks";
import { EmptyState, ErrorState, Score, Skeleton, Time } from "../components/ui";
import { STATUS_LABELS } from "../lib/format";

const COLUMNS = ["shortlisted", "draft_ready", "applied", "recruiter_response", "interview", "offer"];
const CLOSED = ["rejected", "withdrawn", "dismissed", "closed"];

export function ApplicationsPage() {
  const { data, isLoading, error, refetch } = useApplications();
  const [mode, setMode] = useState<"board" | "table">("board");
  const [showClosed, setShowClosed] = useState(false);
  const items = data?.items ?? [];
  const columns = showClosed ? [...COLUMNS, ...CLOSED] : COLUMNS;
  return (
    <>
      <div className="page-head">
        <div><h1>Applications</h1><p>Where each opportunity stands. Change a status from the job's Application tab.</p></div>
        <div className="row">
          <label className="checkbox small"><input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> Show closed</label>
          <div className="segmented" role="tablist">
            <button role="tab" aria-selected={mode === "board"} onClick={() => setMode("board")}>Board</button>
            <button role="tab" aria-selected={mode === "table"} onClick={() => setMode("table")}>Table</button>
          </div>
        </div>
      </div>
      {error && <ErrorState error={error} onRetry={refetch} />}
      {isLoading && <Skeleton height={240} />}
      {data && items.length === 0 && (
        <div className="card"><EmptyState title="No applications yet" action={<Link className="btn primary" to="/jobs?view=needs_review">Review jobs</Link>}>
          Shortlist a job or mark it applied and it appears here.
        </EmptyState></div>
      )}
      {items.length > 0 && mode === "board" && (
        <div className="board">
          {columns.map((status) => {
            const col = items.filter((i) => i.status === status);
            return (
              <section key={status} className="board-col" aria-label={STATUS_LABELS[status]}>
                <h3><span>{STATUS_LABELS[status]}</span><span className="muted">{col.length}</span></h3>
                {col.map((a) => (
                  <Link key={a.id} to={`/jobs/${a.job_id}`} className="board-card">
                    <div className="row between"><strong className="ellipsis">{a.title}</strong><Score value={a.fit_score} /></div>
                    <span className="small muted ellipsis">{a.company}{a.location ? ` · ${a.location}` : ""}</span>
                    <span className="small muted">Updated <Time iso={a.status_changed_at} />{a.next_follow_up_at && <> · follow up <Time iso={a.next_follow_up_at} /></>}</span>
                    {a.verification_status === "closed" && <span className="small" style={{ color: "var(--bad)" }}>Posting closed at source</span>}
                  </Link>
                ))}
              </section>
            );
          })}
        </div>
      )}
      {items.length > 0 && mode === "table" && (
        <div className="card table-wrap">
          <table className="table">
            <thead><tr><th>Job</th><th>Status</th><th>Fit</th><th>Applied</th><th>Last change</th><th>Follow-up</th></tr></thead>
            <tbody>
              {items.filter((a) => showClosed || !CLOSED.includes(a.status)).map((a) => (
                <tr key={a.id}>
                  <td><Link to={`/jobs/${a.job_id}`}>{a.title}</Link><div className="small muted">{a.company}</div></td>
                  <td>{STATUS_LABELS[a.status]}</td>
                  <td><Score value={a.fit_score} /></td>
                  <td><Time iso={a.applied_at} /></td>
                  <td><Time iso={a.status_changed_at} /></td>
                  <td><Time iso={a.next_follow_up_at} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
