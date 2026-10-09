import { useEffect, useRef, useState, type ReactNode } from "react";
import { useProfile, useResumeMutations, useResumes, useSaveProfile } from "../api/hooks";
import type { LocationPreference, ProfileData, Resume } from "../api/types";
import { Badge, EmptyState, ErrorState, ListInput, Modal, Skeleton, Time, errorMessage, useConfirm, useToast } from "../components/ui";
import { title } from "../lib/format";

const SENIORITY = ["intern", "junior", "mid", "senior", "staff", "principal", "lead", "manager", "director"];
const EMPLOYMENT = ["full_time", "contract", "part_time", "internship", "temporary"];

export function ProfilePage() {
  const profile = useProfile();
  const save = useSaveProfile();
  const toast = useToast();
  const [data, setData] = useState<ProfileData | null>(null);
  const [additional, setAdditional] = useState("");
  const loadedVersion = useRef<number | null>(null);

  useEffect(() => {
    if (profile.data && profile.data.version !== loadedVersion.current) {
      setData(structuredClone(profile.data.data));
      setAdditional(profile.data.additional_info ?? "");
      loadedVersion.current = profile.data.version;
    }
  }, [profile.data]);

  if (profile.isLoading || !data) return profile.error ? <ErrorState error={profile.error} onRetry={profile.refetch} /> : <Skeleton height={400} />;
  const dirty = JSON.stringify(data) !== JSON.stringify(profile.data!.data) || additional !== (profile.data!.additional_info ?? "");
  const set = <K extends keyof ProfileData>(key: K, value: ProfileData[K]) => setData({ ...data, [key]: value });
  const submit = () => save.mutate({ data, additional_info: additional, version: profile.data!.version }, {
    onSuccess: (r) => toast(`Profile saved (v${r.version}); ${r.rescored_jobs} jobs re-ranked`),
    onError: (e) => toast(errorMessage(e), "error"),
  });

  return (
    <>
      <div className="page-head">
        <div><h1>Profile & preferences</h1><p>What the agent knows about you and what you want. Nothing is required; every field you fill improves ranking.</p></div>
        <span className="small muted">Version {profile.data!.version} · saved <Time iso={profile.data!.updated_at} /></span>
      </div>
      <div className="two-col">
        <div className="card">
          <div className="card-pad stack">
            <Section title="About you">
              <div className="form-grid">
                <Text label="Name" value={data.name} onChange={(v) => set("name", v)} />
                <Text label="Headline" value={data.headline} onChange={(v) => set("headline", v)} placeholder="Backend engineer, AI platforms" />
                <Text label="Current role" value={data.current_role} onChange={(v) => set("current_role", v)} />
                <Text label="Current company" value={data.current_company} onChange={(v) => set("current_company", v)} />
                <div className="field"><label htmlFor="p-years">Total relevant experience (years)</label>
                  <input id="p-years" className="input" type="number" min={0} max={50} step={0.5} value={data.total_experience_years ?? ""}
                    onChange={(e) => set("total_experience_years", e.target.value === "" ? null : Number(e.target.value))} /></div>
              </div>
              <div className="field"><label htmlFor="p-summary">Professional summary</label>
                <textarea id="p-summary" className="textarea" style={{ minHeight: 80 }} value={data.summary} onChange={(e) => set("summary", e.target.value)} /></div>
            </Section>

            <Section title="What you're looking for">
              <List label="Preferred job titles" value={data.preferred_titles} onChange={(v) => set("preferred_titles", v)} hint="Also used to build search queries." />
              <Chooser label="Target seniority" options={SENIORITY} value={data.seniority_levels} onChange={(v) => set("seniority_levels", v)} />
              <Chooser label="Employment type" options={EMPLOYMENT} value={data.employment_types} onChange={(v) => set("employment_types", v)} />
              <div className="form-grid">
                <List label="Preferred domains / industries" value={data.preferred_domains} onChange={(v) => set("preferred_domains", v)} placeholder="AI/LLM, developer tools, fintech" />
                <List label="Preferred company types / sizes" value={data.preferred_company_types} onChange={(v) => set("preferred_company_types", v)} placeholder="product company, startup" />
                <List label="Skills you want to grow" value={data.growth_skills} onChange={(v) => set("growth_skills", v)} hint="Not counted as gaps." />
                <List label="Search keywords" value={data.search_keywords} onChange={(v) => set("search_keywords", v)} />
              </div>
            </Section>

            <Section title="Locations (most preferred first)">
              <LocationsEditor value={data.locations} onChange={(v) => set("locations", v)} />
            </Section>

            <Section title="Compensation">
              <div className="form-grid">
                <div className="field"><label htmlFor="p-cur">Currency</label>
                  <select id="p-cur" className="select" value={data.compensation.currency} onChange={(e) => set("compensation", { ...data.compensation, currency: e.target.value })}>
                    {["INR", "USD", "EUR", "GBP", "CAD", "AUD", "SGD"].map((c) => <option key={c}>{c}</option>)}
                  </select></div>
                <Num label="Minimum per year" value={data.compensation.min_annual} onChange={(v) => set("compensation", { ...data.compensation, min_annual: v })}
                  hint={data.compensation.currency === "INR" && data.compensation.min_annual ? `= ${(data.compensation.min_annual / 100000).toFixed(1)} LPA` : undefined} />
                <Num label="Target per year" value={data.compensation.target_annual} onChange={(v) => set("compensation", { ...data.compensation, target_annual: v })} />
              </div>
              <p className="hint">Only compared when a posting states a figure. Missing salary never counts against a job.</p>
            </Section>

            <Section title="Exclusions & deal-breakers">
              <div className="form-grid">
                <List label="Excluded companies" value={data.excluded_companies} onChange={(v) => set("excluded_companies", v)} />
                <List label="Deal-breaker phrases" value={data.deal_breakers} onChange={(v) => set("deal_breakers", v)} placeholder="night shift, bond" />
              </div>
              <Text label="Work authorization / sponsorship (optional)" value={data.work_authorization ?? ""} onChange={(v) => set("work_authorization", v || null)} />
            </Section>

            <Section title="Skills">
              <SkillsEditor data={data} set={set} />
            </Section>

            <Section title="Experience">
              <Repeat items={data.experience} onChange={(v) => set("experience", v)} empty={{ title: "", company: "", start: null, end: null, summary: "", technologies: [] }}
                render={(item, update) => (
                  <>
                    <div className="form-grid">
                      <Text label="Title" value={item.title} onChange={(v) => update({ ...item, title: v })} />
                      <Text label="Company" value={item.company} onChange={(v) => update({ ...item, company: v })} />
                      <Text label="Start" value={item.start ?? ""} onChange={(v) => update({ ...item, start: v || null })} placeholder="2024-12" />
                      <Text label="End" value={item.end ?? ""} onChange={(v) => update({ ...item, end: v || null })} placeholder="Present" />
                    </div>
                    <div className="field"><label>Responsibilities & achievements</label><textarea className="textarea" style={{ minHeight: 70 }} value={item.summary} onChange={(e) => update({ ...item, summary: e.target.value })} /></div>
                    <List label="Technologies" value={item.technologies} onChange={(v) => update({ ...item, technologies: v })} />
                  </>
                )} />
            </Section>

            <Section title="Projects">
              <Repeat items={data.projects} onChange={(v) => set("projects", v)} empty={{ name: "", description: "", achievements: [] }}
                render={(item, update) => (
                  <>
                    <Text label="Name" value={item.name} onChange={(v) => update({ ...item, name: v })} />
                    <div className="field"><label>Description</label><textarea className="textarea" style={{ minHeight: 60 }} value={item.description} onChange={(e) => update({ ...item, description: e.target.value })} /></div>
                    <List label="Measurable achievements" value={item.achievements} onChange={(v) => update({ ...item, achievements: v })} />
                  </>
                )} />
            </Section>

            <Section title="Education & certifications">
              <Repeat items={data.education} onChange={(v) => set("education", v)} empty={{ institution: "", degree: "", year: null }} addLabel="Add education"
                render={(item, update) => (
                  <div className="form-grid">
                    <Text label="Institution" value={item.institution} onChange={(v) => update({ ...item, institution: v })} />
                    <Text label="Degree" value={item.degree} onChange={(v) => update({ ...item, degree: v })} />
                    <Text label="Year" value={item.year ?? ""} onChange={(v) => update({ ...item, year: v || null })} />
                  </div>
                )} />
              <Repeat items={data.certifications} onChange={(v) => set("certifications", v)} empty={{ name: "", issuer: "", year: null }} addLabel="Add certification"
                render={(item, update) => (
                  <div className="form-grid">
                    <Text label="Certification" value={item.name} onChange={(v) => update({ ...item, name: v })} />
                    <Text label="Issuer" value={item.issuer} onChange={(v) => update({ ...item, issuer: v })} />
                    <Text label="Year" value={item.year ?? ""} onChange={(v) => update({ ...item, year: v || null })} />
                  </div>
                )} />
            </Section>

            <Section title="Additional information for my job-search agent">
              <textarea className="textarea" aria-label="Additional information for my job-search agent" value={additional} onChange={(e) => setAdditional(e.target.value)}
                placeholder="Anything else: the kind of team you want, what you're avoiding, context for career changes…" />
            </Section>

            <Section title="Ranking thresholds">
              <div className="form-grid">
                <Num label="“Worth reviewing” at priority ≥" value={data.thresholds.review} onChange={(v) => set("thresholds", { ...data.thresholds, review: v ?? 60 })} />
                <Num label="“High priority” at ≥" value={data.thresholds.high_priority} onChange={(v) => set("thresholds", { ...data.thresholds, high_priority: v ?? 75 })} />
                <Num label="Possibly stale after (days unverified)" value={data.thresholds.stale_days} onChange={(v) => set("thresholds", { ...data.thresholds, stale_days: v ?? 14 })} />
              </div>
            </Section>
          </div>
          <div className="sticky-save">
            {dirty && <span className="small muted grow">Unsaved changes</span>}
            <button className="btn" disabled={!dirty} onClick={() => { setData(structuredClone(profile.data!.data)); setAdditional(profile.data!.additional_info ?? ""); }}>Discard</button>
            <button className="btn primary" disabled={!dirty || save.isPending} onClick={submit}>{save.isPending ? "Saving…" : "Save profile"}</button>
          </div>
        </div>
        <div className="stack">
          <ResumesPanel />
          {data.evidence_sources.length > 0 && (
            <section className="card card-pad stack tight">
              <h3>Documents that informed this profile</h3>
              <ul className="bullets small">{data.evidence_sources.map((s, i) => <li key={i}>{s.name}{s.version ? ` v${s.version}` : ""}: {s.sections.join(", ")} · <Time iso={s.applied_at} /></li>)}</ul>
            </section>
          )}
        </div>
      </div>
    </>
  );
}

function Section({ title: heading, children }: { title: string; children: ReactNode }) {
  return <section className="stack tight" aria-label={heading}><h2 style={{ fontSize: 15 }}>{heading}</h2>{children}<div className="divider" style={{ margin: "8px 0 0" }} /></section>;
}

let fieldCounter = 0;
const useFieldId = () => useRef(`f${++fieldCounter}`).current;

function Text({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  const id = useFieldId();
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} className="input" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} /></div>;
}

function Num({ label, value, onChange, hint }: { label: string; value: number | null; onChange: (v: number | null) => void; hint?: string }) {
  const id = useFieldId();
  return (
    <div className="field"><label htmlFor={id}>{label}</label>
      <input id={id} className="input" type="number" min={0} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
      {hint && <span className="hint">{hint}</span>}</div>
  );
}

function List({ label, value, onChange, placeholder, hint }: { label: string; value: string[]; onChange: (v: string[]) => void; placeholder?: string; hint?: string }) {
  const id = useFieldId();
  return <div className="field"><label htmlFor={id}>{label}</label><ListInput id={id} value={value} onChange={onChange} placeholder={placeholder ?? "Comma separated"} />{hint && <span className="hint">{hint}</span>}</div>;
}

function Chooser({ label, options, value, onChange }: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="field"><span className="label">{label}</span>
      <div className="row wrap">{options.map((o) => (
        <button key={o} type="button" className="btn sm" aria-pressed={value.includes(o)}
          onClick={() => onChange(value.includes(o) ? value.filter((v) => v !== o) : [...value, o])}>{title(o)}</button>
      ))}</div></div>
  );
}

function Repeat<T>({ items, onChange, empty, render, addLabel = "Add" }: { items: T[]; onChange: (v: T[]) => void; empty: T; render: (item: T, update: (v: T) => void) => ReactNode; addLabel?: string }) {
  return (
    <div className="stack tight">
      {items.map((item, i) => (
        <div key={i} className="repeat-item">
          {render(item, (v) => onChange(items.map((x, j) => (j === i ? v : x))))}
          <div><button className="btn ghost sm danger" onClick={() => onChange(items.filter((_, j) => j !== i))}>Remove</button></div>
        </div>
      ))}
      <div><button className="btn sm" onClick={() => onChange([...items, structuredClone(empty)])}>+ {addLabel}</button></div>
    </div>
  );
}

function LocationsEditor({ value, onChange }: { value: LocationPreference[]; onChange: (v: LocationPreference[]) => void }) {
  const move = (i: number, d: number) => {
    const next = [...value];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };
  return (
    <div className="stack tight">
      {value.map((loc, i) => (
        <div key={i} className="repeat-item">
          <div className="row"><strong>#{i + 1}</strong>
            <input className="input grow" aria-label="Label" value={loc.label} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
            <button className="btn ghost sm" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
            <button className="btn ghost sm" aria-label="Move down" disabled={i === value.length - 1} onClick={() => move(i, 1)}>↓</button>
            <button className="btn ghost sm danger" aria-label="Remove" onClick={() => onChange(value.filter((_, j) => j !== i))}>✕</button>
          </div>
          <div className="form-grid">
            <div className="field"><label>Work mode</label>
              <select className="select" value={loc.mode} onChange={(e) => onChange(value.map((x, j) => (j === i ? { ...x, mode: e.target.value as LocationPreference["mode"] } : x)))}>
                <option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="onsite">On-site</option><option value="flexible">On-site or hybrid</option>
              </select></div>
            <div className="field"><label>Places</label>
              <ListInput value={loc.places} onChange={(places) => onChange(value.map((x, j) => (j === i ? { ...x, places: places.map((p) => p.toLowerCase()) } : x)))}
                placeholder={loc.mode === "remote" ? "empty = anywhere" : "hyderabad"} /></div>
          </div>
        </div>
      ))}
      <div><button className="btn sm" onClick={() => onChange([...value, { label: "New location", mode: "remote", places: [] }])}>+ Add location</button></div>
    </div>
  );
}

function SkillsEditor({ data, set }: { data: ProfileData; set: <K extends keyof ProfileData>(k: K, v: ProfileData[K]) => void }) {
  const [name, setName] = useState("");
  const add = () => {
    const n = name.trim();
    if (n && !data.skills.some((s) => s.name.toLowerCase() === n.toLowerCase())) set("skills", [...data.skills, { name: n, source: "self" }]);
    setName("");
  };
  return (
    <div className="stack tight">
      <div className="chips">
        {data.skills.map((s) => (
          <span key={s.name} className="chip" title={s.source === "resume" ? "Found in your resume" : s.source === "evidence" ? "From a role/project" : "Added by you"}>
            {s.name}{s.source !== "self" && <span className="muted"> · {s.source}</span>}
            <button className="btn ghost sm" style={{ height: 16, padding: "0 2px" }} aria-label={`Remove ${s.name}`} onClick={() => set("skills", data.skills.filter((x) => x.name !== s.name))}>✕</button>
          </span>
        ))}
      </div>
      <div className="row"><label className="sr-only" htmlFor="skill-new">Add skill</label>
        <input id="skill-new" className="input" placeholder="Add a skill" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
        <button className="btn" onClick={add}>Add</button></div>
      <p className="hint">Skills marked “resume” were found in your resume. Matching never credits a skill that isn't here or in your resume.</p>
    </div>
  );
}

// ---------------------------------------------------------------- resumes

function ResumesPanel() {
  const resumes = useResumes();
  const m = useResumeMutations();
  const toast = useToast();
  const confirm = useConfirm();
  const fileRef = useRef<HTMLInputElement>(null);
  const [reviewing, setReviewing] = useState<Resume | null>(null);
  const [replacing, setReplacing] = useState<Resume | null>(null);

  const upload = (file: File, replaces?: Resume) => {
    const form = new FormData();
    form.append("file", file);
    if (replaces) form.append("replaces_id", String(replaces.id));
    m.upload.mutate(form, {
      onSuccess: (r) => toast(r.extraction_status === "done" ? `Uploaded “${r.display_name}” v${r.version}` : `Uploaded, but text extraction failed: ${r.extraction_error}`, r.extraction_status === "done" ? "info" : "error"),
      onError: (e) => toast(errorMessage(e), "error"),
    });
  };

  return (
    <section className="card card-pad stack" aria-label="Resumes">
      <div className="row between"><h2>Resumes</h2>
        <button className="btn primary" disabled={m.upload.isPending} onClick={() => { setReplacing(null); fileRef.current?.click(); }}>{m.upload.isPending ? "Uploading…" : "Upload"}</button></div>
      <input ref={fileRef} type="file" hidden accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f, replacing ?? undefined); e.target.value = ""; }} />
      <p className="hint">PDF, DOCX or text, up to {Math.round((resumes.data?.max_bytes ?? 5242880) / 1048576)} MB. Stored privately; uploading a new version never replaces the old file.</p>
      {resumes.error && <ErrorState error={resumes.error} onRetry={resumes.refetch} />}
      {resumes.data?.items.length === 0 && <EmptyState title="No resumes yet" />}
      {resumes.data?.items.map((r) => (
        <div key={r.id} className="repeat-item">
          <div className="row between">
            <strong className="ellipsis">{r.display_name}</strong>
            <span className="row">{r.is_default && <Badge tone="accent">Default</Badge>}<Badge>v{r.version}</Badge>{r.source === "drive" && <Badge tone="info">Drive</Badge>}</span>
          </div>
          <span className="small muted">{r.filename} · {Math.max(1, Math.round(r.size_bytes / 1024))} KB · uploaded <Time iso={r.uploaded_at} />{r.purpose ? ` · ${r.purpose}` : ""}</span>
          {r.extraction_status === "failed" && <div className="notice bad small">Couldn't read text: {r.extraction_error}</div>}
          <div className="row wrap">
            {!r.is_default && <button className="btn sm" onClick={() => m.update.mutate({ id: r.id, is_default: true })}>Make default</button>}
            <a className="btn sm" href={`/api/v2/resumes/${r.id}/file`}>Download</a>
            <button className="btn sm" onClick={() => { setReplacing(r); fileRef.current?.click(); }}>Upload new version</button>
            {r.source === "drive" && <button className="btn sm" onClick={() => m.refreshDrive.mutate(r.id, { onSuccess: (x) => toast(x.changed ? "Imported the updated Drive file as a new version" : "No changes in Drive"), onError: (e) => toast(errorMessage(e), "error") })}>Refresh from Drive</button>}
            {r.extraction_status === "done" && (
              <button className="btn sm" disabled={m.extract.isPending} onClick={() => m.extract.mutate(r.id, { onSuccess: (x) => setReviewing(x), onError: (e) => toast(errorMessage(e), "error") })}>
                {m.extract.isPending ? "Reading…" : "Extract to profile…"}
              </button>
            )}
            <button className="btn ghost sm danger" onClick={async () => {
              if (await confirm({ title: "Delete this resume?", body: `“${r.display_name}” v${r.version} will be removed. Drafts that used it keep their text.`, confirmLabel: "Delete", danger: true })) {
                m.remove.mutate(r.id, { onError: (e) => toast(errorMessage(e), "error") });
              }
            }}>Delete</button>
          </div>
        </div>
      ))}
      {reviewing && <ExtractReview resume={reviewing} onClose={() => setReviewing(null)} />}
    </section>
  );
}

const SECTIONS: { key: string; label: string }[] = [
  { key: "skills", label: "Skills" }, { key: "experience", label: "Experience" }, { key: "projects", label: "Projects" },
  { key: "education", label: "Education" }, { key: "certifications", label: "Certifications" }, { key: "summary", label: "Summary" },
  { key: "headline", label: "Headline" }, { key: "total_experience_years", label: "Total experience" },
];

function ExtractReview({ resume, onClose }: { resume: Resume; onClose: () => void }) {
  const p = resume.extracted_profile ?? {};
  const available = SECTIONS.filter((s) => {
    const v = (p as Record<string, unknown>)[s.key];
    return Array.isArray(v) ? v.length > 0 : v !== undefined && v !== null && v !== "";
  });
  const [chosen, setChosen] = useState<string[]>(available.filter((s) => s.key === "skills").map((s) => s.key));
  const m = useResumeMutations();
  const toast = useToast();
  return (
    <Modal title="Review what was found" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!chosen.length || m.apply.isPending} onClick={() => m.apply.mutate({ id: resume.id, sections: chosen }, {
        onSuccess: () => { toast("Added to your profile"); onClose(); }, onError: (e) => toast(errorMessage(e), "error"),
      })}>Add selected to profile</button>
    </>}>
      <p className="small muted">Extracted {p.method === "vocabulary" ? "by keyword matching (no AI key configured)" : `with ${p.method}`}. Nothing changes until you choose. Existing entries are kept; duplicates are skipped.</p>
      {available.length === 0 && <p>Nothing structured could be extracted.</p>}
      {available.map((s) => {
        const v = (p as Record<string, unknown>)[s.key];
        return (
          <label key={s.key} className="repeat-item" style={{ cursor: "pointer" }}>
            <span className="checkbox"><input type="checkbox" checked={chosen.includes(s.key)} onChange={() => setChosen(chosen.includes(s.key) ? chosen.filter((c) => c !== s.key) : [...chosen, s.key])} /> <strong>{s.label}</strong></span>
            <span className="small muted">{Array.isArray(v) ? (typeof v[0] === "string" ? (v as string[]).join(", ") : `${v.length} entr${v.length === 1 ? "y" : "ies"}: ${v.map((x) => (x as { title?: string; name?: string; degree?: string }).title ?? (x as { name?: string }).name ?? (x as { degree?: string }).degree).join("; ")}`) : String(v)}</span>
          </label>
        );
      })}
    </Modal>
  );
}
