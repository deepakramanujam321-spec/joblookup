import { useState } from "react";
import { useFeedbackCategories, useJob, useSubmitFeedback, useUpdateApplication, useVerifyJob } from "../api/hooks";
import type { JobDetail, MatchComponent, WorkflowStatus } from "../api/types";
import { absolute, evidenceLabel, FRESHNESS_LABELS, postedLabel, salaryLabel, SOURCE_LABELS, STATUS_LABELS, title } from "../lib/format";
import { ApplicationWorkspace } from "./ApplicationWorkspace";
import { Badge, Bar, ErrorState, Modal, Score, Skeleton, Time, errorMessage, useConfirm, useToast } from "./ui";

type Tab = "match" | "description" | "application" | "history";

export function JobDetailView({ jobId }: { jobId: number }) {
  const { data, isLoading, error, refetch } = useJob(jobId);
  const [tab, setTab] = useState<Tab>("match");
  if (isLoading) return <div className="card card-pad stack"><Skeleton height={28} width="60%" /><Skeleton height={18} width="40%" /><Skeleton height={200} /></div>;
  if (error) return <div className="card card-pad"><ErrorState error={error} onRetry={refetch} /></div>;
  if (!data) return null;
  return (
    <article className="card" aria-labelledby="job-title">
      <Header detail={data} />
      <div className="tabs" role="tablist">
        {([["match", "Match"], ["description", "Description"], ["application", "Application"], ["history", "History & source"]] as [Tab, string][]).map(([key, label]) => (
          <button key={key} role="tab" className="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      <div className="tab-body" role="tabpanel">
        {tab === "match" && <MatchTab detail={data} />}
        {tab === "description" && <DescriptionTab detail={data} />}
        {tab === "application" && <ApplicationWorkspace detail={data} />}
        {tab === "history" && <HistoryTab detail={data} />}
      </div>
    </article>
  );
}

function Header({ detail }: { detail: JobDetail }) {
  const { job } = detail;
  const update = useUpdateApplication(job.id);
  const verify = useVerifyJob(job.id);
  const confirm = useConfirm();
  const toast = useToast();
  const [feedbackOpen, setFeedbackOpen] = useState<null | "feedback" | "report">(null);
  const status = detail.workflow_status;
  const saved = detail.application?.saved ?? false;
  const freshness = job.priority_explanation?.freshness;
  const salary = salaryLabel(job);
  const closed = job.verification_status === "closed";

  const setStatus = async (target: WorkflowStatus, opts: { confirmText?: string; note?: string } = {}) => {
    if (opts.confirmText && !(await confirm({ title: `${STATUS_LABELS[target]}?`, body: opts.confirmText, confirmLabel: STATUS_LABELS[target] }))) return;
    update.mutate({ status: target, note: opts.note }, {
      onSuccess: () => toast(`Marked as ${STATUS_LABELS[target].toLowerCase()}`),
      onError: (e) => toast(errorMessage(e), "error"),
    });
  };

  return (
    <header className="detail-head">
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div className="stack tight grow">
          <h2 id="job-title" className="detail-title">{job.title}</h2>
          <div className="detail-company">
            {job.company || "Company not stated"}
            {job.company_website && <> · <a href={job.company_website} target="_blank" rel="noopener noreferrer">website</a></>}
          </div>
        </div>
        <div className="stack tight" style={{ alignItems: "flex-end" }}>
          <div className="row"><span className="small muted">Fit</span><Score value={job.fit_score} /></div>
          <div className="row"><span className="small muted">Priority</span><Score value={job.priority_score} /></div>
        </div>
      </div>
      <div className="facts">
        <span>{job.location || "Location not stated"}</span>
        {job.remote_type && <span>{title(job.remote_type)}</span>}
        {job.employment_type && <span>{title(job.employment_type)}</span>}
        {job.seniority && <span>{title(job.seniority)}</span>}
        {job.experience_text && <span title="Extracted from the description">{job.experience_text}</span>}
        {salary && <span title={job.salary_source === "source_structured" ? "Stated by the source" : "Parsed from the listing text"}>{salary}</span>}
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        <Badge tone="accent">{STATUS_LABELS[status]}</Badge>
        <Badge tone={job.posted_at_ts ? "" : "warn"} title={job.posted_at_ts ? `${absolute(job.posted_at_ts)} · ${evidenceLabel(job.posted_at_evidence)}` : "The source didn't provide a publication date"}>
          {postedLabel(job.posted_at_ts)}
        </Badge>
        <Badge title={absolute(job.discovered_at)}>Discovered <Time iso={job.discovered_at} /></Badge>
        {freshness && (
          <Badge tone={freshness.state === "verified" ? "good" : freshness.state === "unchecked" ? "" : "bad"} title={freshness.detail}>
            {FRESHNESS_LABELS[freshness.state]}
            {job.last_verified_at && freshness.state !== "closed" && <> · <Time iso={job.last_verified_at} /></>}
          </Badge>
        )}
        {job.deadline_at && <Badge tone="warn" title={absolute(job.deadline_at)}>Closes <Time iso={job.deadline_at} /></Badge>}
      </div>
      {closed && <div className="notice bad">The source reports this posting is closed{job.closed_at ? ` (detected ${absolute(job.closed_at)})` : ""}. The original link may no longer work.</div>}
      {job.listing_quality === "not_a_listing" && <div className="notice warn">This entry is a search-results page, not an individual job posting. It's kept for reference but excluded from recommendations.</div>}
      <div className="actions">
        <a className="btn primary" href={job.url} target="_blank" rel="noopener noreferrer">View original job ↗</a>
        <button className="btn" aria-pressed={saved} onClick={() => update.mutate({ saved: !saved }, { onError: (e) => toast(errorMessage(e), "error") })}>
          {saved ? "★ Saved" : "☆ Save"}
        </button>
        {["discovered", "needs_review", "dismissed", "closed"].includes(status) && (
          <button className="btn" onClick={() => setStatus("shortlisted")}>Shortlist</button>
        )}
        {!["applied", "recruiter_response", "interview", "offer", "rejected", "withdrawn"].includes(status) && (
          <button className="btn" onClick={() => setStatus("applied", { confirmText: "Only mark this once you've actually submitted the application. Opening the job or drafting an email doesn't count." })}>
            Mark applied
          </button>
        )}
        {!["dismissed", "rejected", "withdrawn"].includes(status) && (
          <button className="btn ghost" onClick={async () => {
            if (await confirm({ title: "Dismiss this job?", body: "It moves to the Dismissed view. You can restore it later, and you'll be asked why so ranking can learn.", confirmLabel: "Dismiss" })) {
              update.mutate({ status: "dismissed" }, { onSuccess: () => setFeedbackOpen("feedback"), onError: (e) => toast(errorMessage(e), "error") });
            }
          }}>Dismiss</button>
        )}
        <button className="btn ghost" onClick={() => setFeedbackOpen("feedback")}>Feedback</button>
        <details className="menu">
          <summary className="btn ghost" aria-label="More actions">More ▾</summary>
          <div className="menu-items" role="menu">
            <button role="menuitem" className="btn ghost" disabled={verify.isPending} title="Check the source right now"
              onClick={() => verify.mutate(undefined, {
                onSuccess: (r) => toast(r.result === "active" ? "Confirmed: still open" : r.result === "closed" ? "The source says it's closed" : `Couldn't confirm: ${r.detail ?? r.result}`),
                onError: (e) => toast(errorMessage(e), "error"),
              })}>
              {verify.isPending ? "Checking…" : "Re-check if still open"}
            </button>
            <button role="menuitem" className="btn ghost" onClick={() => setFeedbackOpen("report")}>Report incorrect info</button>
          </div>
        </details>
      </div>
      {feedbackOpen && <FeedbackDialog jobId={job.id} mode={feedbackOpen} onClose={() => setFeedbackOpen(null)} />}
    </header>
  );
}

function FeedbackDialog({ jobId, mode, onClose }: { jobId: number; mode: "feedback" | "report"; onClose: () => void }) {
  const categories = useFeedbackCategories();
  const submit = useSubmitFeedback(jobId);
  const toast = useToast();
  const [category, setCategory] = useState(mode === "report" ? "inaccurate_listing" : "");
  const [note, setNote] = useState("");
  const options = (categories.data ?? []).filter((c) => (mode === "report" ? ["inaccurate_listing", "duplicate", "other"].includes(c.value) : true));
  return (
    <Modal title={mode === "report" ? "Report incorrect job information" : "How relevant is this job?"} onClose={onClose}
      footer={<>
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!category || submit.isPending} onClick={() => submit.mutate({ category, note: note || undefined }, {
          onSuccess: (r) => {
            toast(r.newly_learned.length ? `Learned: ${r.newly_learned.map((p) => `${p.dimension} “${p.value}”`).join(", ")}` : "Thanks, feedback saved");
            onClose();
          },
          onError: (e) => toast(errorMessage(e), "error"),
        })}>Submit</button>
      </>}>
      <fieldset className="stack tight" style={{ border: 0, padding: 0, margin: 0 }}>
        <legend className="sr-only">Category</legend>
        {options.map((c) => (
          <label key={c.value} className="checkbox">
            <input type="radio" name="feedback" value={c.value} checked={category === c.value} onChange={() => setCategory(c.value)} />
            {c.label}
            {!c.teaches && mode === "feedback" && <span className="small muted">(recorded, doesn't change ranking)</span>}
          </label>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor="fb-note">{mode === "report" ? "What's wrong?" : "Anything else? (optional)"}</label>
        <textarea id="fb-note" className="textarea" style={{ minHeight: 70 }} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
      </div>
      {mode === "feedback" && <p className="small muted">Ranking only changes after at least two similar signals, and your explicit profile preferences always win.</p>}
    </Modal>
  );
}

const COMPONENT_ORDER = ["semantic", "skills", "role", "seniority", "location", "compensation", "domain"];

function MatchTab({ detail }: { detail: JobDetail }) {
  const { assessment, job } = detail;
  const explanation = job.priority_explanation;
  if (!assessment) return <p className="muted">This job hasn't been scored yet. It will be on the next pipeline run.</p>;
  const components = COMPONENT_ORDER.map((k) => [k, assessment.components[k]] as [string, MatchComponent | undefined]).filter(([, c]) => c);
  const semantic = assessment.components.semantic;
  return (
    <div className="two-col">
      <div className="stack">
        <div>
          <div className="section-title">Why this job</div>
          {assessment.rationale && <p style={{ marginTop: 6 }}>{assessment.rationale}</p>}
        </div>
        {assessment.strengths.length > 0 && (
          <div><h3>Strengths</h3><ul className="bullets" style={{ marginTop: 6 }}>{assessment.strengths.map((s) => <li key={s}>{s}</li>)}</ul></div>
        )}
        {assessment.gaps.length > 0 && (
          <div><h3>Gaps & risks</h3><ul className="bullets" style={{ marginTop: 6 }}>{assessment.gaps.map((g) => <li key={g}>{g}</li>)}</ul></div>
        )}
        <div>
          <div className="section-title" style={{ marginBottom: 4 }}>Fit breakdown</div>
          {components.map(([key, c]) => <ComponentRow key={key} c={c!} />)}
          <p className="small muted" style={{ marginTop: 8 }}>
            Fit {Math.round(assessment.overall_score)} is the weighted average of the signals that could be judged; unknowns are skipped, not penalised.
            {semantic?.score == null ? " No AI analysis for this posting; structured signals only." : assessment.model ? ` AI analysis: ${assessment.model}.` : ""}
            {" "}Scoring {assessment.scoring_version}, profile v{assessment.profile_version}.
          </p>
        </div>
      </div>
      <div className="stack">
        <div className="card card-pad stack tight">
          <div className="section-title">Priority {job.priority_score != null ? Math.round(job.priority_score) : "—"}</div>
          {explanation?.reasons.map((r, i) => (
            <div key={i} className="reason"><span>{r.label}</span>
              <span className={i === 0 ? "" : r.delta >= 0 ? "delta-pos" : "delta-neg"}>{i === 0 ? Math.round(r.delta) : `${r.delta > 0 ? "+" : ""}${r.delta}`}</span>
            </div>
          ))}
          <p className="small muted">Priority = fit, adjusted for how recently it was posted, whether it's confirmed open, and what you've taught it.</p>
        </div>
        <div className="card card-pad stack tight">
          <div className="section-title">Signals kept separate</div>
          <dl className="kv">
            <dt>Candidate fit</dt><dd>{Math.round(assessment.overall_score)} / 100</dd>
            <dt>Recency</dt><dd>{explanation?.recency.state === "unknown" ? "Unknown: no posting date from source" : `${title(explanation?.recency.state)} (${explanation?.recency.age_days} days)`}</dd>
            <dt>Freshness</dt><dd>{explanation ? `${FRESHNESS_LABELS[explanation.freshness.state]}: ${explanation.freshness.detail}` : "—"}</dd>
          </dl>
        </div>
      </div>
    </div>
  );
}

function ComponentRow({ c }: { c: MatchComponent }) {
  return (
    <div className="component">
      <div>
        <div style={{ fontWeight: 600 }}>{c.label}</div>
        <div className="small muted">{c.detail}</div>
        {(c.matches.length > 0 || c.gaps.length > 0) && (
          <div className="chips" style={{ marginTop: 4 }}>
            {c.matches.slice(0, 8).map((m) => <span key={`m-${m}`} className="chip good">{m}</span>)}
            {c.gaps.slice(0, 6).map((g) => <span key={`g-${g}`} className="chip gap">{g}</span>)}
          </div>
        )}
      </div>
      <div style={{ textAlign: "right" }}>{c.score == null ? <span className="small muted" title="Not enough information to judge">n/a</span> : <strong>{c.score}</strong>}</div>
      {c.score != null && <Bar value={c.score} />}
    </div>
  );
}

const DESCRIPTION_SOURCE: Record<string, string> = {
  greenhouse_api: "Greenhouse job board API", lever_api: "Lever postings API", ashby_api: "Ashby job board API",
  remoteok_api: "RemoteOK API", wwr_rss: "WeWorkRemotely RSS feed", jsonld: "Employer's structured job data", page_text: "Text captured from the job page",
};

function DescriptionTab({ detail }: { detail: JobDetail }) {
  const { job } = detail;
  const html = job.description_html;
  return (
    <div className="stack">
      <div className="row wrap between">
        <span className="small muted">Original description · from {DESCRIPTION_SOURCE[job.description_source ?? ""] ?? job.description_source ?? "the source"}</span>
        <a className="btn sm" href={job.url} target="_blank" rel="noopener noreferrer">View original ↗</a>
      </div>
      {job.description_is_partial && (
        <div className="notice warn">This may be a partial description captured from {SOURCE_LABELS[job.source] ?? job.source}. Read the full posting on the original site before applying.</div>
      )}
      {!html && !job.description && <p className="muted">No description is available for this listing. Open the original posting to read it.</p>}
      {/* Sanitized server-side (nh3 allow-list: structure + links only); never AI-generated. */}
      {html ? <div className="prose" dangerouslySetInnerHTML={{ __html: html }} /> : job.description && <div className="prose prose-plain">{job.description}</div>}
      {job.skills.length > 0 && (
        <div><div className="section-title">Technologies mentioned</div><div className="chips" style={{ marginTop: 6 }}>{job.skills.map((s) => <span className="chip" key={s}>{s}</span>)}</div></div>
      )}
    </div>
  );
}

function HistoryTab({ detail }: { detail: JobDetail }) {
  const { job } = detail;
  return (
    <div className="two-col">
      <div className="stack">
        <div className="section-title">Dates</div>
        <dl className="kv">
          <dt>Posted</dt><dd>{job.posted_at_ts ? <>{absolute(job.posted_at_ts)} <span className="muted">· {evidenceLabel(job.posted_at_evidence)}</span></> : "Posting date unavailable: the source didn't provide one"}</dd>
          {job.source_updated_at && <><dt>Last updated at source</dt><dd>{absolute(job.source_updated_at)}</dd></>}
          <dt>First discovered</dt><dd>{absolute(job.discovered_at)}</dd>
          <dt>Last seen in collection</dt><dd>{job.last_seen_at ? absolute(job.last_seen_at) : "—"}</dd>
          <dt>Last confirmed open</dt><dd>{job.last_verified_at ? absolute(job.last_verified_at) : "Never verified"}</dd>
          <dt>Last checked</dt><dd>{job.last_checked_at ? absolute(job.last_checked_at) : "Never"}</dd>
          {job.deadline_at && <><dt>Application deadline</dt><dd>{absolute(job.deadline_at)}</dd></>}
        </dl>
        <div className="section-title">Verification checks</div>
        {detail.verifications.length === 0 ? <p className="muted small">No checks yet.</p> : (
          <ul className="timeline">
            {detail.verifications.map((v) => (
              <li key={v.id}><span><strong>{title(v.result)}</strong> · {v.method}{v.http_status ? ` (HTTP ${v.http_status})` : ""}{v.detail ? ` · ${v.detail}` : ""} · <Time iso={v.checked_at} /></span></li>
            ))}
          </ul>
        )}
      </div>
      <div className="stack">
        <div className="section-title">Source & provenance</div>
        <dl className="kv">
          <dt>Source</dt><dd>{SOURCE_LABELS[job.source] ?? job.source}</dd>
          <dt>Listing ID</dt><dd className="mono">{job.external_id || "—"}</dd>
          <dt>Original URL</dt><dd><a href={job.url} target="_blank" rel="noopener noreferrer">{job.url}</a></dd>
        </dl>
        {detail.sources.length > 1 && (
          <><div className="small muted">Also seen at:</div>
            <ul className="bullets small">{detail.sources.filter((s) => s.url !== job.url).map((s) => <li key={s.id}>{SOURCE_LABELS[s.source] ?? s.source}: <a href={s.url} target="_blank" rel="noopener noreferrer">{s.url}</a></li>)}</ul></>
        )}
        <div className="section-title">Your feedback</div>
        {detail.feedback.length === 0 ? <p className="muted small">None yet.</p> : (
          <ul className="timeline">{detail.feedback.map((f) => <li key={f.id}><span>{title(f.category)}{f.note ? `: “${f.note}”` : ""} · <Time iso={f.created_at} /></span></li>)}</ul>
        )}
      </div>
    </div>
  );
}
