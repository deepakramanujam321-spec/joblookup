// Typed contracts for the FastAPI /api/v2 endpoints (see webapp/routers).
// Timestamps are ISO-8601 strings; null means "unknown", never "none".

export type WorkflowStatus =
  | "discovered" | "needs_review" | "shortlisted" | "draft_ready" | "applied" | "recruiter_response"
  | "interview" | "offer" | "rejected" | "withdrawn" | "dismissed" | "closed";

export type SalaryStatus = "not_stated" | "meets" | "below" | "stated";

export type VerificationStatus = "unchecked" | "active" | "closed" | "source_unavailable" | "verification_failed";

export interface JobListItem {
  id: number;
  title: string;
  company: string;
  location: string | null;
  remote_type: string | null;
  employment_type: string | null;
  seniority: string | null;
  source: string;
  salary_text: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_period: string | null;
  posted_at_ts: string | null;
  posted_at_evidence: string | null;
  discovered_at: string;
  last_verified_at: string | null;
  last_checked_at: string | null;
  verification_status: VerificationStatus;
  listing_quality: string;
  fit_score: number | null;
  priority_score: number | null;
  match_highlights: { matched_skills?: string[]; gaps?: string[]; confidence?: number; semantic?: boolean; salary?: SalaryStatus } | null;
  deadline_at: string | null;
  skills: string[];
  workflow_status: WorkflowStatus;
  saved: boolean;
  summary: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface MatchComponent {
  label: string;
  score: number | null;
  weight: number;
  detail: string;
  matches: string[];
  gaps: string[];
}

export interface Assessment {
  id: number;
  overall_score: number;
  llm_score: number | null;
  model: string | null;
  scoring_version: string;
  profile_version: number;
  components: Record<string, MatchComponent>;
  strengths: string[];
  gaps: string[];
  rationale: string | null;
  created_at: string;
}

export interface PriorityExplanation {
  reasons: { label: string; delta: number }[];
  recency: { state: "new" | "recent" | "aging" | "old" | "unknown"; age_days: number | null };
  freshness: { state: "verified" | "unchecked" | "possibly_stale" | "closed" | "not_a_listing"; detail: string };
}

export interface Job extends Omit<JobListItem, "workflow_status" | "saved" | "summary"> {
  url: string;
  canonical_url: string | null;
  external_id: string | null;
  company_website: string | null;
  job_category: string | null;
  experience_min_years: number | null;
  experience_text: string | null;
  salary_source: string | null;
  description: string | null;
  description_html: string | null;
  description_source: string | null;
  description_is_partial: boolean;
  posted_at: string | null;
  source_updated_at: string | null;
  last_seen_at: string | null;
  verification_failures: number;
  closed_at: string | null;
  fit_rationale: string | null;
  priority_explanation: PriorityExplanation | null;
  scoring_version: string | null;
  status: string;
}

export interface ApplicationEvent { id: number; kind: string; from_status: string | null; to_status: string | null; note: string | null; at: string }
export interface ApplicationTask { id: number; kind: "interview" | "follow_up" | "reminder"; title: string; due_at: string | null; done_at: string | null; notes: string | null }

export interface Application {
  id: number;
  status: WorkflowStatus;
  saved: boolean;
  resume_id: number | null;
  application_url: string | null;
  notes: string | null;
  recruiter_name: string | null;
  recruiter_contact: string | null;
  applied_at: string | null;
  status_changed_at: string;
  next_follow_up_at: string | null;
  final_draft_id: number | null;
  events: ApplicationEvent[];
  tasks: ApplicationTask[];
}

export interface JobDetail {
  job: Job;
  workflow_status: WorkflowStatus;
  application: Application | null;
  assessment: Assessment | null;
  drafts: { id: number; version: number; origin: string; created_at: string; gmail_status: string }[];
  feedback: { id: number; category: string; note: string | null; created_at: string }[];
  sources: { id: number; source: string; url: string; external_id: string | null; first_seen_at: string; last_seen_at: string }[];
  verifications: { id: number; checked_at: string; result: VerificationStatus; method: string | null; http_status: number | null; detail: string | null }[];
  thresholds: Thresholds;
}

export interface Thresholds { review: number; high_priority: number; stale_days: number }

export interface Run {
  id: number;
  run_type: string;
  started_at: string;
  finished_at: string | null;
  status: "ok" | "partial" | "failed";
  duration_ms: number | null;
  jobs_found: number | null;
  jobs_new: number | null;
  jobs_duplicate: number | null;
  jobs_rejected: number | null;
  jobs_updated: number | null;
  source_stats: Record<string, { found: number; error: string | null }> | null;
  details: Record<string, unknown> | null;
  error: string | null;
}

export interface Overview {
  new_this_week: number;
  new_last_run: number;
  worth_reviewing: number;
  high_priority: number;
  in_progress: number;
  awaiting_feedback: number;
  saved: number;
  interviews_scheduled: number;
  upcoming_tasks: { id: number; kind: string; title: string; due_at: string | null; job_id: number; job_title: string; company: string }[];
  last_collect: Run | null;
  thresholds: Thresholds;
}

export interface Draft {
  id: number;
  job_id: number;
  version: number;
  origin: "generated" | "edited" | "legacy";
  parent_id: number | null;
  subject: string | null;
  body: string;
  cover_letter: string | null;
  qualifications: { requirement: string; evidence: string }[];
  missing_info: string[];
  answers: { question: string; answer: string | null; needs_input: boolean }[];
  resume_id: number | null;
  model: string | null;
  gmail_status: "not_saved" | "saved" | "failed" | "missing";
  gmail_saved_at: string | null;
  gmail_error: string | null;
  gmail_open_url: string | null;
  created_at: string;
}

export interface LearnedPreference {
  id: number;
  dimension: string;
  value: string;
  weight: number;
  positive: number;
  negative: number;
  active: boolean;
  disabled_by_user: boolean;
  explanation: string | null;
  updated_at: string;
}

export interface Insights {
  preferences: LearnedPreference[];
  active: LearnedPreference[];
  feedback_counts: Record<string, number>;
  recent_feedback: { id: number; job_id: number; category: string; note: string | null; created_at: string; title: string; company: string }[];
  evaluation: { status: "ok" | "insufficient_data"; labelled: number; message: string; auc_without_learning?: number; auc_with_learning?: number; improvement?: number };
  learning_reset_at: string | null;
  rules: { min_evidence: number; scoring_version: string };
}

export interface LocationPreference { label: string; mode: "remote" | "hybrid" | "onsite" | "flexible"; places: string[] }
export interface SkillEntry { name: string; level?: string | null; years?: number | null; source: string }

export interface ProfileData {
  name: string;
  email: string;
  headline: string;
  current_role: string;
  current_company: string;
  summary: string;
  total_experience_years: number | null;
  experience: { title: string; company: string; start: string | null; end: string | null; summary: string; technologies: string[] }[];
  skills: SkillEntry[];
  projects: { name: string; description: string; achievements: string[] }[];
  education: { institution: string; degree: string; year: string | null }[];
  certifications: { name: string; issuer: string; year: string | null }[];
  preferred_titles: string[];
  preferred_domains: string[];
  preferred_company_types: string[];
  locations: LocationPreference[];
  compensation: { currency: string; min_annual: number | null; target_annual: number | null };
  employment_types: string[];
  seniority_levels: string[];
  growth_skills: string[];
  excluded_companies: string[];
  deal_breakers: string[];
  search_keywords: string[];
  work_authorization: string | null;
  thresholds: Thresholds;
  weights: Record<string, number>;
  evidence_sources: { type: string; resume_id?: number; name: string; version?: number; sections: string[]; applied_at: string }[];
}

export interface ProfileRecord { owner: string; data: ProfileData; additional_info: string; version: number; updated_at: string; learning_reset_at: string | null }

export interface Resume {
  id: number;
  display_name: string;
  purpose: string | null;
  version: number;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  source: "upload" | "drive" | "seed";
  drive_file_id: string | null;
  drive_modified_time: string | null;
  is_default: boolean;
  extraction_status: "pending" | "done" | "failed";
  extraction_error: string | null;
  extracted_profile: (Partial<ProfileData> & { skills?: string[]; method?: string }) | null;
  uploaded_at: string;
  deleted_at: string | null;
}

export interface IntegrationService { status: "connected" | "error" | "revoked"; account_email: string | null; connected_at: string; last_error: string | null; scopes: string[] }
export interface IntegrationStatus { configured: boolean; redirect_uri: string; picker_ready: boolean; services: { gmail: IntegrationService | null; drive: IntegrationService | null } }

export interface KnowledgeDoc { id: number; name: string; mime_type: string | null; status: string; error: string | null; imported_at: string; refreshed_at: string | null; drive_modified_time: string | null; chars: number | null }

export interface PipelineHealth {
  state: "healthy" | "degraded" | "failing" | "stale" | "unknown";
  message: string;
  runs: Record<string, { last: Run | null; last_success_at: string | null }>;
  listings: { total: number; active: number; closed: number; stale: number; unchecked: number; not_a_listing: number };
  recent_runs: Run[];
}

export interface Facets { sources: string[]; seniority: string[]; remote_type: string[]; employment_type: string[]; statuses: WorkflowStatus[]; views: string[] }
