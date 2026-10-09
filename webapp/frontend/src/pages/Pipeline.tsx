import { usePipeline } from "../api/hooks";
import { Badge, ErrorState, Skeleton, Time } from "../components/ui";
import { absolute, title } from "../lib/format";

export function PipelinePage() {
  const { data, isLoading, error, refetch } = usePipeline();
  if (isLoading) return <Skeleton height={300} />;
  if (error || !data) return <ErrorState error={error} onRetry={refetch} />;
  const tone = data.state === "healthy" ? "good" : data.state === "degraded" ? "warn" : data.state === "unknown" ? "" : "bad";
  return (
    <>
      <div className="page-head">
        <div><h1>Pipeline health</h1><p>{data.message}</p></div>
        <Badge tone={tone}>{title(data.state)}</Badge>
      </div>
      <div className="stats" style={{ marginBottom: 20 }}>
        {[["Listings", data.listings.total], ["Confirmed open", data.listings.active], ["Closed", data.listings.closed], ["Possibly stale", data.listings.stale], ["Not yet verified", data.listings.unchecked], ["Not job postings", data.listings.not_a_listing]].map(([label, value]) => (
          <div key={label} className="stat"><span className="stat-value">{value}</span><span className="stat-label">{label}</span></div>
        ))}
      </div>
      <div className="stats" style={{ marginBottom: 20 }}>
        {Object.entries(data.runs).map(([type, info]) => (
          <div key={type} className="stat">
            <span className="stat-label">{title(type)}</span>
            <span>{info.last ? <><Badge tone={info.last.status === "ok" ? "good" : info.last.status === "partial" ? "warn" : "bad"}>{info.last.status}</Badge> <Time iso={info.last.finished_at ?? info.last.started_at} /></> : <span className="muted">Never run</span>}</span>
            <span className="small muted">Last success: {info.last_success_at ? <Time iso={info.last_success_at} /> : "never"}</span>
          </div>
        ))}
      </div>
      <section className="card table-wrap" aria-label="Run history">
        <table className="table">
          <thead><tr><th>Run</th><th>When</th><th>Status</th><th>Duration</th><th>Found</th><th>New</th><th>Duplicates</th><th>Rejected</th><th>Details</th></tr></thead>
          <tbody>
            {data.recent_runs.map((r) => {
              const failures = r.source_stats ? Object.entries(r.source_stats).filter(([, s]) => s.error) : [];
              return (
                <tr key={r.id}>
                  <td>{title(r.run_type)}</td>
                  <td title={absolute(r.started_at)}><Time iso={r.started_at} /></td>
                  <td><Badge tone={r.status === "ok" ? "good" : r.status === "partial" ? "warn" : "bad"}>{r.status}</Badge></td>
                  <td>{r.duration_ms != null ? `${Math.round(r.duration_ms / 1000)}s` : "—"}</td>
                  <td>{r.jobs_found ?? "—"}</td><td>{r.jobs_new ?? "—"}</td><td>{r.jobs_duplicate ?? "—"}</td><td>{r.jobs_rejected ?? "—"}</td>
                  <td className="small">
                    {r.error && <div style={{ color: "var(--bad)" }}>{r.error.slice(0, 200)}</div>}
                    {failures.map(([name, s]) => <div key={name} style={{ color: "var(--warn)" }}>{name}: {s.error}</div>)}
                    {r.source_stats && !failures.length && <span className="muted">{Object.entries(r.source_stats).map(([n, s]) => `${n} ${s.found}`).join(" · ")}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </>
  );
}
