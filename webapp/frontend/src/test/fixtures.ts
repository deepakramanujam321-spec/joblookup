import type { JobDetail, JobListItem } from "../api/types";

export const listItem = (over: Partial<JobListItem> = {}): JobListItem => ({
  id: 1, title: "Senior Backend Engineer", company: "Acme", location: "Remote - India", remote_type: "remote",
  employment_type: "full_time", seniority: "senior", source: "lever", salary_text: null, salary_min: null, salary_max: null,
  salary_currency: null, salary_period: null, posted_at_ts: null, posted_at_evidence: null,
  discovered_at: new Date().toISOString(), last_verified_at: null, last_checked_at: null, verification_status: "unchecked",
  listing_quality: "ok", fit_score: 82, priority_score: 85, match_highlights: null, deadline_at: null, skills: ["Python"],
  workflow_status: "needs_review", saved: false, summary: "Strong match: Python.", ...over,
});

export const detail = (over: Partial<JobDetail["job"]> = {}): JobDetail => ({
  job: {
    ...listItem(), url: "https://jobs.lever.co/acme/1", canonical_url: null, external_id: "1", company_website: null,
    job_category: "backend", experience_min_years: 4, experience_text: "4+ years of experience", salary_source: null,
    description: "Python FastAPI", description_html: "<h3>Requirements</h3><ul><li>Python</li></ul>", description_source: "lever_api",
    description_is_partial: false, posted_at: null, source_updated_at: null, last_seen_at: null, verification_failures: 0,
    closed_at: null, fit_rationale: "Good fit.", scoring_version: "v2.0", status: "scored",
    priority_explanation: { reasons: [{ label: "Candidate fit", delta: 82 }, { label: "Posted in the last 3 days", delta: 6 }], recency: { state: "unknown", age_days: null }, freshness: { state: "unchecked", detail: "Not verified yet" } },
    ...over,
  },
  workflow_status: "needs_review",
  application: null,
  assessment: {
    id: 1, overall_score: 82, llm_score: 80, model: "openai/gpt-4o-mini", scoring_version: "v2.0", profile_version: 1,
    components: { skills: { label: "Skills alignment", score: 67, weight: 0.2, detail: "2 of 3 specific skills…", matches: ["Python", "FastAPI"], gaps: ["Kubernetes"] },
      compensation: { label: "Compensation", score: null, weight: 0.05, detail: "The posting doesn't state compensation.", matches: [], gaps: [] } },
    strengths: ["Strong match: Python, FastAPI"], gaps: ["Potential gap: Kubernetes"], rationale: "Good fit.", created_at: new Date().toISOString(),
  },
  drafts: [], feedback: [], sources: [{ id: 1, source: "lever", url: "https://jobs.lever.co/acme/1", external_id: "1", first_seen_at: "", last_seen_at: "" }],
  verifications: [], thresholds: { review: 60, high_priority: 75, stale_days: 14 },
});
