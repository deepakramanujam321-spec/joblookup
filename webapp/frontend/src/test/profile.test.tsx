import { screen } from "@testing-library/react";
import { ProfilePage } from "../pages/Profile";
import { absolute, inputToIso, relative, shortDate, toMillis } from "../lib/format";
import type { ProfileData, ProfileRecord } from "../api/types";
import { mockApi, renderAt } from "./utils";

const blank = (): ProfileData => ({
  name: "", email: "", headline: "", current_role: "", current_company: "", summary: "", total_experience_years: null,
  experience: [], skills: [], projects: [], education: [], certifications: [],
  preferred_titles: [], preferred_domains: [], preferred_company_types: [], locations: [],
  compensation: { currency: "INR", min_annual: null, target_annual: null },
  employment_types: ["full_time"], seniority_levels: [], growth_skills: [], excluded_companies: [], deal_breakers: [], search_keywords: [],
  work_authorization: null, thresholds: { review: 60, high_priority: 75, stale_days: 14 }, weights: {}, resume_folder_url: null, evidence_sources: [],
});

const populated = (): ProfileData => ({
  ...blank(),
  name: "Asha Rao", email: "asha@example.com", headline: "Backend engineer", current_role: "Engineer", current_company: "Acme",
  summary: "Builds APIs.", total_experience_years: 4.5,
  experience: [{ title: "Engineer", company: "Acme", start: "2022-01", end: null, summary: "APIs", technologies: ["Python"] }],
  skills: [{ name: "Python", source: "resume" }, { name: "FastAPI", source: "evidence" }, { name: "SQL", source: "self" }],
  projects: [{ name: "JobLookup", description: "Job search", achievements: ["Cut latency 40%"] }],
  education: [{ institution: "IIT", degree: "BTech", year: "2020" }],
  certifications: [{ name: "AWS SA", issuer: "Amazon", year: null }],
  preferred_titles: ["Backend Engineer"], seniority_levels: ["mid"],
  locations: [
    { label: "Remote (India)", mode: "remote", places: ["india"] }, { label: "Hyderabad", mode: "flexible", places: ["hyderabad"] },
    { label: "Bengaluru", mode: "flexible", places: ["bengaluru", "bangalore"] }, { label: "Remote (global)", mode: "remote", places: [] },
    { label: "Pune", mode: "flexible", places: ["pune"] },
  ],
  compensation: { currency: "INR", min_annual: 1800000, target_annual: null },
  resume_folder_url: "https://drive.google.com/drive/folders/abc",
  evidence_sources: [{ type: "resume", resume_id: 1, name: "Master resume", version: 2, sections: ["skills", "experience"], applied_at: "2026-11-20T10:00:00.123456+00:00" }],
});

const record = (data: ProfileData, over: Partial<ProfileRecord> = {}): ProfileRecord => ({
  owner: "default", data, additional_info: "", version: 3, updated_at: "2026-11-25T05:51:53.948132+00:00", learning_reset_at: null, ...over,
});

const resume = (over = {}) => ({
  id: 1, display_name: "Master resume", purpose: null, version: 2, filename: "resume.pdf", content_type: "application/pdf", size_bytes: 52_000,
  sha256: "x", source: "drive", drive_file_id: "d1", drive_modified_time: "Nov 20, 2026", is_default: true, extraction_status: "done",
  extraction_error: null, extracted_profile: null, uploaded_at: "2026-11-20T10:00:00.123456+00:00", deleted_at: null, ...over,
});

const hub = (synced_at: unknown) => ({
  folder_url: "https://drive.google.com/drive/folders/abc",
  last_sync: { at: "2026-11-25T05:51:53.948132+00:00", failed: [], skipped: [], updated: [], imported: [], unchanged: ["Master resume"], default_set_to: null, synced_at },
});

function mount(profile: ProfileRecord, hubBody: unknown, resumes: unknown[] = [resume()]) {
  mockApi({
    "GET /api/v2/profile": profile,
    "GET /api/v2/resumes": { items: resumes, max_bytes: 5242880, llm_configured: true },
    "GET /api/v2/resumes/hub": hubBody,
  });
  return renderAt("/profile", "/profile", <ProfilePage />);
}

describe("ProfilePage renders for every realistic payload", () => {
  it("populated original account with a hub sync", async () => {
    mount(record(populated()), hub("2026-11-25T05:51:53.948132+00:00"));
    expect(await screen.findByText("Profile & preferences")).toBeInTheDocument();
    expect(await screen.findByText(/1 unchanged/)).toBeInTheDocument();
    expect(screen.getByText("Master resume")).toBeInTheDocument();
  });

  it.each([
    ["postgres text format (space, +00)", "2026-11-25 05:51:53.948132+00"],
    ["unparsable text", "not a date"],
    ["null", null],
    ["number", 1764049913],
    ["object", { $date: 1 }],
  ])("odd synced_at/updated_at: %s", async (_name, odd) => {
    mount(record(populated(), { updated_at: odd as string }), hub(odd), [resume({ uploaded_at: odd })]);
    expect(await screen.findByText("Profile & preferences")).toBeInTheDocument();
    expect(await screen.findByText(/Last sync/)).toBeInTheDocument();
  });

  it("minimal blank profile with no resumes and no hub sync", async () => {
    mount(record(blank(), { owner: "priya" }), { folder_url: null, last_sync: null }, []);
    expect(await screen.findByText("Profile & preferences")).toBeInTheDocument();
    expect(await screen.findByText("No resumes yet")).toBeInTheDocument();
  });

  it("an evidence source from an older version without sections or applied_at", async () => {
    const data = populated();
    data.evidence_sources = [{ type: "resume", name: "Old" } as ProfileData["evidence_sources"][number]];
    mount(record(data), hub("2026-11-25T05:51:53.948132+00:00"));
    expect(await screen.findByText("Profile & preferences")).toBeInTheDocument();
    expect(await screen.findByText(/Documents that informed this profile/)).toBeInTheDocument();
  });

  it("duplicate skill names (resume extraction can repeat a skill)", async () => {
    const data = populated();
    data.skills = [{ name: "Python", source: "resume" }, { name: "Python", source: "evidence" }];
    mount(record(data), hub("2026-11-25T05:51:53.948132+00:00"));
    expect(await screen.findByText("Profile & preferences")).toBeInTheDocument();
  });

  it("a browser without structuredClone (Safari < 15.4, Chrome < 98)", async () => {
    vi.stubGlobal("structuredClone", undefined);
    mount(record(populated()), hub("2026-11-25T05:51:53.948132+00:00"));
    expect(await screen.findByText("Profile & preferences")).toBeInTheDocument();
  });
});

describe("date formatting never throws", () => {
  it("reads Postgres' text timestamps and shows placeholders for junk", () => {
    const now = Date.parse("2026-11-25T06:00:00Z");
    expect(relative("2026-11-25 05:51:53.948132+00", now)).toBe("8 minutes ago");
    expect(toMillis("2026-11-25 05:51:53+05:30")).toBe(Date.parse("2026-11-25T05:51:53+05:30"));
    for (const junk of ["", "garbage", "2026-13-45", null, undefined]) {
      expect(relative(junk as string, now)).toBeNull();
      expect(() => absolute(junk as string)).not.toThrow();
      expect(() => shortDate(junk as string)).not.toThrow();
    }
    expect(absolute("garbage")).toBe("Unknown date");
    expect(shortDate("garbage")).toBe("—");
    expect(inputToIso("garbage")).toBeNull();
    expect(inputToIso("2026-11-25", "09:00:00")).toMatch(/^2026-11-25T/);
    expect(toMillis(1e20)).toBeNull();
  });
});
