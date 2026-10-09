import { Link } from "react-router-dom";
import { useInsightMutations, useInsights } from "../api/hooks";
import { Badge, EmptyState, ErrorState, Skeleton, Time, errorMessage, useConfirm, useToast } from "../components/ui";
import { title } from "../lib/format";

export function InsightsPage() {
  const { data, isLoading, error, refetch } = useInsights();
  const m = useInsightMutations();
  const confirm = useConfirm();
  const toast = useToast();
  if (isLoading) return <Skeleton height={300} />;
  if (error || !data) return <ErrorState error={error} onRetry={refetch} />;
  const learning = data.preferences.filter((p) => !p.active);
  const ev = data.evaluation;
  return (
    <>
      <div className="page-head">
        <div><h1>What we learned</h1><p>How your feedback is changing recommendations, and whether it's helping.</p></div>
        <button className="btn danger" disabled={m.reset.isPending} onClick={async () => {
          if (await confirm({ title: "Reset learned preferences?", body: "Ranking goes back to your profile and fit alone. Your past feedback stays visible but stops influencing ranking.", confirmLabel: "Reset", danger: true })) {
            m.reset.mutate(undefined, { onSuccess: () => toast("Learning reset"), onError: (e) => toast(errorMessage(e), "error") });
          }
        }}>Reset learning</button>
      </div>
      <div className="two-col">
        <div className="stack">
          <section className="card" aria-label="Active adjustments">
            <div className="card-head"><h2>Active adjustments</h2><span className="small muted">Need ≥ {data.rules.min_evidence} consistent signals</span></div>
            {data.active.length === 0 ? (
              <EmptyState title="No learned adjustments yet">Use <strong>Feedback</strong> on jobs (e.g. “Wrong seniority”, “Excellent match”). Once a pattern repeats, ranking adapts here.</EmptyState>
            ) : (
              <div className="card-pad stack">
                {data.active.map((p) => (
                  <div key={p.id} className="row between" style={{ alignItems: "flex-start" }}>
                    <div className="grow">
                      <div className="row"><Badge tone={p.weight > 0 ? "good" : "bad"}>{p.weight > 0 ? "+" : ""}{Number(p.weight).toFixed(1)}</Badge>
                        <strong>{title(p.dimension)}: {p.dimension === "category" ? title(p.value) : p.value}</strong></div>
                      <p className="small muted" style={{ marginTop: 4 }}>{p.explanation}</p>
                    </div>
                    <button className="btn sm" onClick={() => m.toggle.mutate({ id: p.id, disabled: true })}>Turn off</button>
                  </div>
                ))}
              </div>
            )}
          </section>
          {learning.length > 0 && (
            <section className="card card-pad stack" aria-label="Not active">
              <h2>Not active</h2>
              {learning.map((p) => (
                <div key={p.id} className="row between" style={{ alignItems: "flex-start" }}>
                  <div className="grow">
                    <strong>{title(p.dimension)}: {p.dimension === "category" ? title(p.value) : p.value}</strong>
                    <span className="small muted"> · {p.positive} positive / {p.negative} negative</span>
                    <p className="small muted">{p.disabled_by_user ? "Turned off by you." : p.explanation}</p>
                  </div>
                  {p.disabled_by_user && <button className="btn sm" onClick={() => m.toggle.mutate({ id: p.id, disabled: false })}>Turn on</button>}
                </div>
              ))}
            </section>
          )}
        </div>
        <div className="stack">
          <section className="card card-pad stack tight" aria-label="Is it working">
            <h2>Is it working?</h2>
            {ev.status === "ok" ? (
              <>
                <dl className="kv">
                  <dt>Ranking quality without learning</dt><dd>{Math.round((ev.auc_without_learning ?? 0) * 100)}%</dd>
                  <dt>With learning</dt><dd>{Math.round((ev.auc_with_learning ?? 0) * 100)}%</dd>
                  <dt>Labelled jobs</dt><dd>{ev.labelled}</dd>
                </dl>
                <p className="small">{ev.message}</p>
                <p className="hint">Measured as how often a job you liked ranks above one you didn't (50% = chance). Each job is scored using only feedback from <em>other</em> jobs, so it can't grade itself.</p>
              </>
            ) : <p className="small muted">{ev.message}</p>}
          </section>
          <section className="card card-pad stack tight" aria-label="Recent feedback">
            <h2>Recent feedback</h2>
            {data.recent_feedback.length === 0 && <p className="small muted">None yet.</p>}
            {data.recent_feedback.map((f) => (
              <div key={f.id} className="row between">
                <span className="grow ellipsis"><Link to={`/jobs/${f.job_id}`}>{f.title}</Link> <span className="muted">· {title(f.category)}</span></span>
                <span className="small muted"><Time iso={f.created_at} /></span>
                <button className="btn ghost sm" aria-label="Delete feedback" onClick={() => m.deleteFeedback.mutate(f.id)}>✕</button>
              </div>
            ))}
            {data.learning_reset_at && <p className="hint">Learning was reset <Time iso={data.learning_reset_at} />; earlier feedback doesn't count.</p>}
          </section>
        </div>
      </div>
    </>
  );
}
