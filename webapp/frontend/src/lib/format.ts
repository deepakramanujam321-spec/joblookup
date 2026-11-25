// Presentation helpers. Relative times are computed at render time; the
// exact timestamp is always available as a tooltip (title attribute).

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Epoch ms for any timestamp the API may send, or null. Never throws.
 *  Also reads Postgres' text form ("2026-11-25 05:51:53.948132+00"), which
 *  Safari/Firefox reject and Chrome only tolerates. */
export function toMillis(value: unknown): number | null {
  if (typeof value === "number") return Math.abs(value) <= 8.64e15 ? value : null; // JS Date range; also rejects NaN/Infinity
  if (typeof value !== "string" || !value.trim()) return null;
  const text = value.trim();
  let t = Date.parse(text);
  if (Number.isNaN(t)) {
    const m = text.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)(?:\.(\d+))?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$/i);
    if (!m) return null;
    const [, day, time, frac = "", zone = "Z"] = m;
    const offset = zone.toUpperCase() === "Z" ? "Z" : zone.length === 3 ? `${zone}:00` : zone.length === 5 ? `${zone.slice(0, 3)}:${zone.slice(3)}` : zone;
    t = Date.parse(`${day}T${time.length === 5 ? `${time}:00` : time}${frac ? `.${frac.slice(0, 3).padEnd(3, "0")}` : ""}${offset}`);
  }
  return Number.isNaN(t) ? null : t;
}

export function relative(iso: string | null | undefined, now: number = Date.now()): string | null {
  const t = toMillis(iso);
  if (t === null) return null;
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
  const t = toMillis(iso);
  if (t === null) return "Unknown date";
  try {
    return new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return new Date(t).toISOString().slice(0, 16).replace("T", " ");
  }
}

/** Calendar date only ("25 Nov 2026"); a placeholder when unparsable. */
export function shortDate(iso: string | null | undefined): string {
  const t = toMillis(iso);
  if (t === null) return "—";
  try {
    return new Date(t).toLocaleDateString();
  } catch {
    return new Date(t).toISOString().slice(0, 10);
  }
}

/** ISO string for an <input type=date|datetime-local> value, or null when
 *  it isn't a real date (toISOString throws on an Invalid Date). */
export function inputToIso(value: string, timeIfDateOnly = ""): string | null {
  if (!value) return null;
  const t = Date.parse(value.length === 10 && timeIfDateOnly ? `${value}T${timeIfDateOnly}` : value);
  return Number.isNaN(t) ? null : new Date(t).toISOString();
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
  if (j.verification_status === "active" && j.last_verified_at && Date.now() - (toMillis(j.last_verified_at) ?? 0) <= staleDays * DAY) return "verified";
  if (j.last_verified_at || j.verification_status === "verification_failed" || j.verification_status === "source_unavailable") return "possibly_stale";
  return "unchecked";
}

export function scoreTone(score: number | null | undefined): "high" | "mid" | "low" | "none" {
  if (score == null) return "none";
  if (score >= 75) return "high";
  if (score >= 55) return "mid";
  return "low";
}
