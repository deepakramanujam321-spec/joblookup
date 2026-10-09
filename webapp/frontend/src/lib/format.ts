// Presentation helpers. Relative times are computed at render time; the
// exact timestamp is always available as a tooltip (title attribute).

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function relative(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const diff = now - t;
  const future = diff < 0;
  const abs = Math.abs(diff);
  let text: string;
  if (abs < MINUTE) text = "just now";
  else if (abs < HOUR) text = plural(Math.round(abs / MINUTE), "minute");
  else if (abs < DAY) text = plural(Math.round(abs / HOUR), "hour");
  else if (abs < 2 * DAY) return future ? "tomorrow" : "yesterday";
  else if (abs < 30 * DAY) text = plural(Math.round(abs / DAY), "day");
  else if (abs < 365 * DAY) text = plural(Math.round(abs / (30 * DAY)), "month");
  else text = plural(Math.round(abs / (365 * DAY)), "year");
  if (text === "just now") return text;
  return future ? `in ${text}` : `${text} ago`;
}

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

export function absolute(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function postedLabel(iso: string | null | undefined): string {
  const rel = relative(iso);
  return rel ? `Posted ${rel}` : "Posting date unavailable";
}

const EVIDENCE: Record<string, string> = {
  "greenhouse_api.first_published": "Greenhouse job board API (first published)",
  "lever_api.createdAt": "Lever postings API (created)",
  "ashby_api.publishedAt": "Ashby job board API (published)",
  "remoteok_api.date": "RemoteOK API",
  "wwr_rss.pubDate": "WeWorkRemotely RSS feed",
  "jsonld.datePosted": "Employer's structured job data (schema.org)",
};

export function evidenceLabel(evidence: string | null | undefined): string {
  if (!evidence) return "No source date available";
  return EVIDENCE[evidence] ?? evidence;
}

export const STATUS_LABELS: Record<string, string> = {
  discovered: "Discovered", needs_review: "Needs review", shortlisted: "Shortlisted", draft_ready: "Draft ready",
  applied: "Applied", recruiter_response: "Recruiter response", interview: "Interview", offer: "Offer",
  rejected: "Rejected", withdrawn: "Withdrawn", dismissed: "Dismissed", closed: "Closed / expired",
};

export const SOURCE_LABELS: Record<string, string> = {
  greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby", remoteok: "RemoteOK", weworkremotely: "WeWorkRemotely",
  linkedin: "LinkedIn", indeed: "Indeed",
};

export const FRESHNESS_LABELS: Record<string, string> = {
  verified: "Verified open", unchecked: "Not verified yet", possibly_stale: "Possibly stale", closed: "Closed",
  not_a_listing: "Not a job posting",
};

export function title(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function money(amount: number, currency: string | null): string {
  const cur = currency || "";
  if (cur === "INR") {
    if (amount >= 100_000) return `₹${(amount / 100_000).toFixed(amount % 100_000 === 0 ? 0 : 1)}L`;
    return `₹${amount.toLocaleString("en-IN")}`;
  }
  const symbol = { USD: "$", EUR: "€", GBP: "£" }[cur] ?? "";
  if (amount >= 1000) return `${symbol}${Math.round(amount / 1000)}k${symbol ? "" : ` ${cur}`}`;
  return `${symbol}${amount}${symbol ? "" : ` ${cur}`}`;
}

export function salaryLabel(j: { salary_min: number | null; salary_max: number | null; salary_currency: string | null; salary_period: string | null; salary_text: string | null }): string | null {
  if (j.salary_min != null) {
    const per = j.salary_period && j.salary_period !== "year" ? ` / ${j.salary_period}` : "";
    const range = j.salary_max != null && j.salary_max !== j.salary_min
      ? `${money(j.salary_min, j.salary_currency)}–${money(j.salary_max, j.salary_currency)}`
      : money(j.salary_min, j.salary_currency);
    return `${range}${per}`;
  }
  return j.salary_text || null;
}

/** Freshness from list fields (the detail view gets the server's version). */
export function listFreshness(j: { verification_status: string; last_verified_at: string | null; listing_quality: string }, staleDays = 14): string {
  if (j.listing_quality === "not_a_listing") return "not_a_listing";
  if (j.verification_status === "closed") return "closed";
  if (j.verification_status === "active" && j.last_verified_at && Date.now() - Date.parse(j.last_verified_at) <= staleDays * DAY) return "verified";
  if (j.last_verified_at || j.verification_status === "verification_failed" || j.verification_status === "source_unavailable") return "possibly_stale";
  return "unchecked";
}

export function scoreTone(score: number | null | undefined): "high" | "mid" | "low" | "none" {
  if (score == null) return "none";
  if (score >= 75) return "high";
  if (score >= 55) return "mid";
  return "low";
}
