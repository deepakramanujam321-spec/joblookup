import { evidenceLabel, listFreshness, postedLabel, relative, salaryLabel } from "../lib/format";
import { DEFAULT_QUERY, fromSearchParams, remember, toSearchParams } from "../lib/filters";

describe("format", () => {
  const now = Date.parse("2026-10-09T12:00:00Z");
  it("formats relative times without inventing dates", () => {
    expect(relative("2026-10-09T08:00:00Z", now)).toBe("4 hours ago");
    expect(relative("2026-10-07T12:00:00Z", now)).toBe("2 days ago");
    expect(relative("2026-10-08T11:00:00Z", now)).toBe("yesterday");
    expect(relative(null, now)).toBeNull();
    expect(postedLabel(null)).toBe("Posting date unavailable");
  });
  it("labels evidence honestly", () => {
    expect(evidenceLabel(null)).toBe("No source date available");
    expect(evidenceLabel("lever_api.createdAt")).toMatch(/Lever/);
  });
  it("formats salaries", () => {
    expect(salaryLabel({ salary_min: 1800000, salary_max: 2500000, salary_currency: "INR", salary_period: "year", salary_text: null })).toBe("₹18L–₹25L");
    expect(salaryLabel({ salary_min: 120000, salary_max: 150000, salary_currency: "USD", salary_period: "year", salary_text: null })).toBe("$120k–$150k");
    expect(salaryLabel({ salary_min: null, salary_max: null, salary_currency: null, salary_period: null, salary_text: null })).toBeNull();
  });
  it("derives freshness without treating unknown as stale", () => {
    expect(listFreshness({ verification_status: "unchecked", last_verified_at: null, listing_quality: "ok" })).toBe("unchecked");
    expect(listFreshness({ verification_status: "closed", last_verified_at: null, listing_quality: "ok" })).toBe("closed");
    expect(listFreshness({ verification_status: "active", last_verified_at: new Date().toISOString(), listing_quality: "ok" })).toBe("verified");
  });
});

describe("filter state", () => {
  beforeEach(() => localStorage.clear());
  it("round-trips through the URL", () => {
    const q = { ...DEFAULT_QUERY, view: "saved", min_score: 70, q: "python", page: 2 };
    expect(fromSearchParams(toSearchParams(q))).toEqual(q);
  });
  it("remembers filters but not the page or search text", () => {
    remember({ ...DEFAULT_QUERY, view: "needs_review", remote_type: "remote", q: "x", page: 4 });
    const restored = fromSearchParams(new URLSearchParams());
    expect(restored.view).toBe("needs_review");
    expect(restored.remote_type).toBe("remote");
    expect(restored.page).toBe(1);
    expect(restored.q).toBeUndefined();
  });
});
