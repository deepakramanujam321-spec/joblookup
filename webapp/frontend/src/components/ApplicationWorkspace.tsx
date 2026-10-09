import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useDraftMutations, useDrafts, useIntegrations, useResumes, useTaskMutations, useUpdateApplication } from "../api/hooks";
import type { Draft, JobDetail, WorkflowStatus } from "../api/types";
import { absolute, STATUS_LABELS, title } from "../lib/format";
import { Badge, EmptyState, ErrorState, Skeleton, Time, errorMessage, useConfirm, useToast } from "./ui";

const STATUS_FLOW: WorkflowStatus[] = ["needs_review", "shortlisted", "draft_ready", "applied", "recruiter_response", "interview", "offer", "rejected", "withdrawn", "dismissed", "closed"];

export function ApplicationWorkspace({ detail }: { detail: JobDetail }) {
  return (
    <div className="two-col">
      <DraftsPanel detail={detail} />
      <div className="stack">
        <TrackingPanel detail={detail} />
        <TasksPanel detail={detail} />
        <TimelinePanel detail={detail} />
      </div>
    </div>
  );
}

function TrackingPanel({ detail }: { detail: JobDetail }) {
  const app = detail.application;
  const update = useUpdateApplication(detail.job.id);
  const resumes = useResumes();
  const toast = useToast();
  const confirm = useConfirm();
  const [form, setForm] = useState({ notes: "", application_url: "", recruiter_name: "", recruiter_contact: "", next_follow_up_at: "" });
  useEffect(() => {
    setForm({
      notes: app?.notes ?? "", application_url: app?.application_url ?? "", recruiter_name: app?.recruiter_name ?? "",
      recruiter_contact: app?.recruiter_contact ?? "", next_follow_up_at: app?.next_follow_up_at?.slice(0, 10) ?? "",
    });
  }, [app?.id, app?.notes, app?.application_url, app?.recruiter_name, app?.recruiter_contact, app?.next_follow_up_at]);
  const dirty = form.notes !== (app?.notes ?? "") || form.application_url !== (app?.application_url ?? "") ||
    form.recruiter_name !== (app?.recruiter_name ?? "") || form.recruiter_contact !== (app?.recruiter_contact ?? "") ||
    form.next_follow_up_at !== (app?.next_follow_up_at?.slice(0, 10) ?? "");

  const changeStatus = async (status: WorkflowStatus) => {
    if (status === "applied" && !(await confirm({ title: "Mark as applied?", body: "Only do this once you've actually submitted the application.", confirmLabel: "Mark applied" }))) return;
    update.mutate({ status }, { onSuccess: () => toast(`Status: ${STATUS_LABELS[status]}`), onError: (e) => toast(errorMessage(e), "error") });
  };
  const save = () => update.mutate({
    notes: form.notes || null, application_url: form.application_url || null, recruiter_name: form.recruiter_name || null,
    recruiter_contact: form.recruiter_contact || null, next_follow_up_at: form.next_follow_up_at ? new Date(`${form.next_follow_up_at}T09:00:00`).toISOString() : null,
  }, { onSuccess: () => toast("Saved"), onError: (e) => toast(errorMessage(e), "error") });

  return (
    <section className="card card-pad stack" aria-label="Application tracking">
      <div className="row between"><h3>Tracking</h3>{app?.applied_at && <Badge tone="good" title={absolute(app.applied_at)}>Applied <Time iso={app.applied_at} /></Badge>}</div>
      <div className="field">
        <label htmlFor="app-status">Status</label>
        <select id="app-status" className="select" value={detail.workflow_status} disabled={update.isPending} onChange={(e) => changeStatus(e.target.value as WorkflowStatus)}>
          {!STATUS_FLOW.includes(detail.workflow_status) && <option value={detail.workflow_status}>{STATUS_LABELS[detail.workflow_status]}</option>}
          {STATUS_FLOW.map((s) => <option key={s} value={s}>{STATUS_LABELS[s]}</option>)}
        </select>
        <span className="hint">Changes that don't make sense (e.g. interview before applying) are refused.</span>
      </div>
      <div className="field">
        <label htmlFor="app-resume">Resume for this application</label>
        <select id="app-resume" className="select" value={app?.resume_id ?? ""} onChange={(e) => update.mutate({ resume_id: e.target.value ? Number(e.target.value) : null }, { onError: (err) => toast(errorMessage(err), "error") })}>
          <option value="">Default resume</option>
          {resumes.data?.items.map((r) => <option key={r.id} value={r.id}>{r.display_name} (v{r.version})</option>)}
        </select>
      </div>
      <div className="field"><label htmlFor="app-url">Application URL (if different)</label>
        <input id="app-url" className="input" type="url" placeholder="https://" value={form.application_url} onChange={(e) => setForm({ ...form, application_url: e.target.value })} /></div>
      <div className="form-grid">
        <div className="field"><label htmlFor="app-rname">Recruiter</label><input id="app-rname" className="input" value={form.recruiter_name} onChange={(e) => setForm({ ...form, recruiter_name: e.target.value })} /></div>
        <div className="field"><label htmlFor="app-rcontact">Recruiter contact</label><input id="app-rcontact" className="input" value={form.recruiter_contact} onChange={(e) => setForm({ ...form, recruiter_contact: e.target.value })} /></div>
      </div>
      <div className="field"><label htmlFor="app-follow">Next follow-up</label><input id="app-follow" className="input" type="date" value={form.next_follow_up_at} onChange={(e) => setForm({ ...form, next_follow_up_at: e.target.value })} /></div>
      <div className="field"><label htmlFor="app-notes">Notes</label><textarea id="app-notes" className="textarea" style={{ minHeight: 90 }} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
      <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn primary" disabled={!dirty || update.isPending} onClick={save}>Save details</button></div>
    </section>
  );
}

function TasksPanel({ detail }: { detail: JobDetail }) {
  const tasks = detail.application?.tasks ?? [];
  const m = useTaskMutations(detail.job.id);
  const toast = useToast();
  const [draft, setDraft] = useState({ kind: "interview", title: "", due: "" });
  const add = () => m.create.mutate({ kind: draft.kind, title: draft.title, due_at: draft.due ? new Date(draft.due).toISOString() : null }, {
    onSuccess: () => setDraft({ kind: "interview", title: "", due: "" }),
    onError: (e) => toast(errorMessage(e), "error"),
  });
  return (
    <section className="card card-pad stack" aria-label="Interviews and reminders">
      <h3>Interviews & reminders</h3>
      {tasks.length === 0 && <p className="small muted">Nothing scheduled.</p>}
      {tasks.map((t) => (
        <div key={t.id} className="row between">
          <label className="checkbox grow">
            <input type="checkbox" checked={!!t.done_at} onChange={() => m.update.mutate({ id: t.id, done: !t.done_at })} />
            <span style={{ textDecoration: t.done_at ? "line-through" : undefined }}><Badge>{title(t.kind)}</Badge> {t.title}</span>
          </label>
          <span className="small muted">{t.due_at ? <Time iso={t.due_at} /> : "No date"}</span>
          <button className="btn ghost sm" aria-label={`Delete ${t.title}`} onClick={() => m.remove.mutate(t.id)}>✕</button>
        </div>
      ))}
      <div className="stack tight">
        <div className="row">
          <label className="sr-only" htmlFor="task-kind">Type</label>
          <select id="task-kind" className="select" style={{ width: 130 }} value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
            <option value="interview">Interview</option><option value="follow_up">Follow-up</option><option value="reminder">Reminder</option>
          </select>
          <label className="sr-only" htmlFor="task-title">Title</label>
          <input id="task-title" className="input grow" placeholder="e.g. Technical screen" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
        </div>
        <div className="row">
          <label className="sr-only" htmlFor="task-due">When</label>
          <input id="task-due" className="input grow" type="datetime-local" value={draft.due} onChange={(e) => setDraft({ ...draft, due: e.target.value })} />
          <button className="btn" disabled={!draft.title.trim() || m.create.isPending} onClick={add}>Add</button>
        </div>
      </div>
    </section>
  );
}

function TimelinePanel({ detail }: { detail: JobDetail }) {
  const events = detail.application?.events ?? [];
  if (!events.length) return null;
  return (
    <section className="card card-pad stack tight" aria-label="Activity">
      <h3>Activity</h3>
      <ul className="timeline">
        {events.map((e) => (
          <li key={e.id}><span>
            {e.kind === "status_change" ? <>{STATUS_LABELS[e.from_status ?? ""] ?? e.from_status} → <strong>{STATUS_LABELS[e.to_status ?? ""] ?? e.to_status}</strong></> : e.note}
            {e.kind === "status_change" && e.note ? ` · ${e.note}` : ""} · <Time iso={e.at} />
          </span></li>
        ))}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------ drafts

const GMAIL_STATUS: Record<Draft["gmail_status"], { label: string; tone: "" | "good" | "warn" | "bad" }> = {
  not_saved: { label: "Not in Gmail", tone: "" },
  saved: { label: "Saved as Gmail draft", tone: "good" },
  failed: { label: "Gmail save failed", tone: "bad" },
  missing: { label: "No longer in Gmail (sent or deleted)", tone: "warn" },
};

function DraftsPanel({ detail }: { detail: JobDetail }) {
  const jobId = detail.job.id;
  const drafts = useDrafts(jobId);
  const m = useDraftMutations(jobId);
  const resumes = useResumes();
  const integrations = useIntegrations();
  const toast = useToast();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [genOpts, setGenOpts] = useState({ resume_id: detail.application?.resume_id ?? null as number | null, cover: false, questions: "" });
  const items = drafts.data?.items ?? [];
  const current = items.find((d) => d.id === selectedId) ?? items[0];
  const [edit, setEdit] = useState({ subject: "", body: "", cover_letter: "" });
  useEffect(() => {
    if (current) setEdit({ subject: current.subject ?? "", body: current.body, cover_letter: current.cover_letter ?? "" });
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (drafts.isLoading) return <div className="card card-pad"><Skeleton height={220} /></div>;
  if (drafts.error) return <div className="card card-pad"><ErrorState error={drafts.error} onRetry={drafts.refetch} /></div>;

  const dirty = current && (edit.subject !== (current.subject ?? "") || edit.body !== current.body || edit.cover_letter !== (current.cover_letter ?? ""));
  const gmailConnected = integrations.data?.services.gmail?.status === "connected";
  const generate = () => m.generate.mutate({
    resume_id: genOpts.resume_id, include_cover_letter: genOpts.cover,
    questions: genOpts.questions.split("\n").map((q) => q.trim()).filter(Boolean),
  }, { onSuccess: (d) => { setSelectedId(d.id); toast(`Draft v${d.version} generated`); }, onError: (e) => toast(errorMessage(e), "error") });
  const saveEdit = () => m.save.mutate({ subject: edit.subject || null, body: edit.body, cover_letter: edit.cover_letter || null, parent_id: current?.id ?? null }, {
    onSuccess: (d) => { setSelectedId(d.id); toast(`Saved as v${d.version}`); }, onError: (e) => toast(errorMessage(e), "error"),
  });
  const copy = async () => {
    const text = [edit.subject && `Subject: ${edit.subject}`, edit.body, edit.cover_letter && `\n---\n\n${edit.cover_letter}`].filter(Boolean).join("\n\n");
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied to clipboard");
    } catch {
      toast("Couldn't access the clipboard; select the text and copy manually.", "error");
    }
  };

  return (
    <section className="card card-pad stack" aria-label="Application drafts">
      <div className="row between">
        <h3>Application draft</h3>
        {items.length > 0 && (
          <select className="select" style={{ width: "auto" }} aria-label="Version" value={current?.id} onChange={(e) => setSelectedId(Number(e.target.value))}>
            {items.map((d) => <option key={d.id} value={d.id}>v{d.version} · {d.origin === "edited" ? "your edit" : d.origin === "legacy" ? "from digest" : "generated"} · {new Date(d.created_at).toLocaleDateString()}</option>)}
          </select>
        )}
      </div>

      {!drafts.data?.llm_configured && <div className="notice warn">Draft generation needs an LLM provider key on the server (e.g. OPENAI_API_KEY). You can still write and save drafts yourself.</div>}

      <details open={items.length === 0}>
        <summary style={{ cursor: "pointer", fontWeight: 600 }}>{items.length ? "Generate a new version" : "Generate a draft"}</summary>
        <div className="stack tight" style={{ marginTop: 8 }}>
          <div className="field">
            <label htmlFor="gen-resume">Based on resume</label>
            <select id="gen-resume" className="select" value={genOpts.resume_id ?? ""} onChange={(e) => setGenOpts({ ...genOpts, resume_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Default resume</option>
              {resumes.data?.items.map((r) => <option key={r.id} value={r.id}>{r.display_name} (v{r.version})</option>)}
            </select>
          </div>
          <label className="checkbox"><input type="checkbox" checked={genOpts.cover} onChange={(e) => setGenOpts({ ...genOpts, cover: e.target.checked })} /> Include a cover letter</label>
          <div className="field">
            <label htmlFor="gen-q">Application questions (optional, one per line)</label>
            <textarea id="gen-q" className="textarea" style={{ minHeight: 60 }} value={genOpts.questions} onChange={(e) => setGenOpts({ ...genOpts, questions: e.target.value })} placeholder="Why do you want to work here?" />
          </div>
          <div className="row">
            <button className="btn primary" disabled={!drafts.data?.llm_configured || m.generate.isPending} onClick={generate}>{m.generate.isPending ? "Generating…" : "Generate"}</button>
            <span className="hint">Uses only facts from your resume and profile. Your edited versions are never overwritten.</span>
          </div>
        </div>
      </details>

      {current ? (
        <>
          <div className="field"><label htmlFor="d-subject">Subject</label><input id="d-subject" className="input" value={edit.subject} onChange={(e) => setEdit({ ...edit, subject: e.target.value })} /></div>
          <div className="field"><label htmlFor="d-body">Email</label><textarea id="d-body" className="textarea" style={{ minHeight: 220 }} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} /></div>
          {(current.cover_letter || edit.cover_letter) && (
            <div className="field"><label htmlFor="d-cover">Cover letter</label><textarea id="d-cover" className="textarea" style={{ minHeight: 220 }} value={edit.cover_letter} onChange={(e) => setEdit({ ...edit, cover_letter: e.target.value })} /></div>
          )}
          <div className="row wrap">
            <button className="btn primary" disabled={!dirty || !edit.body.trim() || m.save.isPending} onClick={saveEdit}>Save as new version</button>
            <button className="btn" onClick={copy}>Copy</button>
            {gmailConnected ? (
              <button className="btn" disabled={!!dirty || m.gmail.isPending} title={dirty ? "Save your edits first" : "Saves a draft in Gmail; nothing is sent"}
                onClick={() => m.gmail.mutate({ draftId: current.id }, { onSuccess: () => toast("Saved to Gmail drafts (not sent)"), onError: (e) => toast(errorMessage(e), "error") })}>
                {m.gmail.isPending ? "Saving…" : current.gmail_status === "saved" ? "Saved in Gmail ✓" : "Save to Gmail drafts"}
              </button>
            ) : (
              <Link className="btn ghost" to="/settings">Connect Gmail to save drafts</Link>
            )}
          </div>
          <div className="row wrap" style={{ gap: 6 }}>
            <Badge tone={GMAIL_STATUS[current.gmail_status].tone}>{GMAIL_STATUS[current.gmail_status].label}</Badge>
            {current.gmail_status === "saved" && current.gmail_open_url && <a className="small" href={current.gmail_open_url} target="_blank" rel="noopener noreferrer">Open in Gmail ↗</a>}
            {current.gmail_status === "saved" && <button className="btn ghost sm" onClick={() => m.refreshGmail.mutate(current.id)}>Check Gmail</button>}
            {current.gmail_error && current.gmail_status !== "saved" && <span className="small muted">{current.gmail_error}</span>}
            <span className="small muted">Draft ≠ application: mark the job as applied once you've submitted it.</span>
          </div>
          {current.missing_info.length > 0 && (
            <div className="notice warn"><div><strong>Needs your input before sending</strong><ul className="bullets small" style={{ marginTop: 4 }}>{current.missing_info.map((x) => <li key={x}>{x}</li>)}</ul></div></div>
          )}
          {current.qualifications.length > 0 && (
            <div><div className="section-title">Relevant qualifications (from your resume)</div>
              <ul className="bullets small" style={{ marginTop: 6 }}>{current.qualifications.map((q) => <li key={q.requirement}><strong>{q.requirement}</strong>: {q.evidence}</li>)}</ul></div>
          )}
          {current.answers.length > 0 && (
            <div className="stack tight"><div className="section-title">Application answers</div>
              {current.answers.map((a) => (
                <div key={a.question}><div style={{ fontWeight: 600 }}>{a.question}</div>
                  {a.needs_input || !a.answer ? <div className="small" style={{ color: "var(--warn)" }}>Your profile doesn't cover this; answer it yourself.</div> : <div className="small">{a.answer}</div>}</div>
              ))}
            </div>
          )}
        </>
      ) : (
        <EmptyState title="No draft yet">Generate one from your resume, or write your own below.</EmptyState>
      )}
      {!current && (
        <div className="stack tight">
          <textarea className="textarea" aria-label="Write a draft" placeholder="Write your application email…" value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} />
          <div><button className="btn" disabled={!edit.body.trim() || m.save.isPending} onClick={saveEdit}>Save draft</button></div>
        </div>
      )}
    </section>
  );
}
