"""Candidate-job fit: an explainable breakdown, not a single opaque number.

Each component scores one question (skills, role family, seniority,
location, compensation, domain, and the LLM's semantic read) on 0-100, or
returns None when the information needed to judge it is missing. Missing
is never scored as a negative: the overall fit is the weighted mean of the
components that *could* be judged, and `confidence` reports how much of the
total weight that covered.

Everything here is pure and deterministic. The only model call (semantic
fit) happens upstream in assessment.py and arrives here as plain data, so
filtering, sorting or re-ranking never triggers an LLM call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import normalize

SCORING_VERSION = "v2.1"  # v2.1: stated pay below the minimum caps fit

DEFAULT_WEIGHTS = {
    "semantic": 0.30,
    "skills": 0.20,
    "role": 0.10,
    "location": 0.15,
    "seniority": 0.10,
    "compensation": 0.10,
    "domain": 0.05,
}
# A posting that *states* pay below the candidate's minimum can't be a
# strong match, whatever else fits: fit is capped here (below the digest
# and "worth reviewing" thresholds). Unstated pay is never penalised.
BELOW_SALARY_FLOOR_CAP = 40.0

LABELS = {
    "semantic": "Overall fit (AI read of the full posting)",
    "skills": "Skills alignment",
    "role": "Role & category alignment",
    "location": "Location & remote compatibility",
    "seniority": "Seniority & experience",
    "compensation": "Compensation",
    "domain": "Domain & technology focus",
}

LOCATION_RANK_SCORES = [100, 90, 80, 70, 60, 55, 50]

# Approximate, static conversion rates. Only used to compare a posted
# salary against a floor in another currency, and always labelled
# "estimated" in the output when used.
APPROX_TO_INR = {"INR": 1.0, "USD": 84.0, "EUR": 92.0, "GBP": 108.0, "CAD": 61.0, "AUD": 55.0, "SGD": 63.0}

_RESTRICTED_REGION_RE = re.compile(
    r"\b(us|usa|u\.s\.|united states|canada|uk|united kingdom|europe|emea|eu|latam|americas|north america|"
    r"germany|france|spain|poland|brazil|mexico|australia|netherlands|ireland|portugal)\b"
)
_INDIA_RE = re.compile(r"\b(india|apac|asia|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|delhi|ncr|noida|gurgaon|gurugram)\b")
_ANYWHERE_RE = re.compile(r"\b(anywhere|worldwide|global|international)\b")


@dataclass
class Component:
    score: float | None
    detail: str
    matches: list[str]
    gaps: list[str]
    blocking: bool = False  # a stated fact that rules the job out (e.g. pay below minimum)

    def to_dict(self, name: str, weight: float) -> dict:
        return {
            "label": LABELS[name], "score": None if self.score is None else round(self.score),
            "weight": weight, "detail": self.detail, "matches": self.matches, "gaps": self.gaps,
        }


def _unknown(detail: str) -> Component:
    return Component(None, detail, [], [])


# ------------------------------------------------------------------- skills


def candidate_skill_set(profile: dict, resume_text: str) -> set[str]:
    """Skills the candidate has evidence for: listed in the profile, or
    found in the resume text by the same vocabulary used on postings."""
    listed = {s["name"] for s in profile.get("skills", []) if s.get("name")}
    canonical = set(normalize.extract_skills(" ".join(listed)))
    return canonical | listed | set(normalize.extract_skills(resume_text))


def score_skills(job: dict, candidate_skills: set[str], growth: set[str]) -> Component:
    job_skills = list(job.get("skills") or [])
    if not job_skills:
        return _unknown("No recognisable skills were extracted from this posting.")
    lowered_candidate = {s.lower() for s in candidate_skills}
    matched = [s for s in job_skills if s.lower() in lowered_candidate]
    missing = [s for s in job_skills if s.lower() not in lowered_candidate and not normalize.is_generic_skill(s)]
    growth_hits = [s for s in missing if s.lower() in {g.lower() for g in growth}]
    gaps = [s for s in missing if s not in growth_hits]
    denominator = len(matched) + len(gaps) + 0.5 * len(growth_hits)
    if denominator == 0:
        return _unknown("Only generic skills were listed in this posting.")
    score = 100 * len(matched) / denominator
    detail = f"{len(matched)} of {len(matched) + len(missing)} specific skills in the posting are documented in your profile or resume."
    if growth_hits:
        detail += f" {', '.join(growth_hits)} {'is' if len(growth_hits) == 1 else 'are'} on your growth list."
    return Component(score, detail, matched, gaps)


# --------------------------------------------------------------------- role


def preferred_categories(profile: dict) -> set[str]:
    return {normalize.detect_category(t) for t in profile.get("preferred_titles", [])} - {"other"}


def score_role(job: dict, profile: dict) -> Component:
    titles = profile.get("preferred_titles", [])
    if not titles:
        return _unknown("No preferred job titles set in your profile.")
    title = (job.get("title") or "").lower()
    category = job.get("job_category") or normalize.detect_category(job.get("title"))
    for preferred in titles:
        if preferred.lower() in title:
            return Component(100, f"Title matches your target title “{preferred}”.", [preferred], [])
    cats = preferred_categories(profile)
    if category in cats:
        return Component(80, f"Role family “{category.replace('_', '/')}” is one of your targets.", [category], [])
    if category == "other":
        return Component(45, "Role family couldn't be determined from the title.", [], [])
    return Component(
        15, f"Role family “{category.replace('_', '/')}” isn't one of your target families.", [],
        [f"Role family: {category.replace('_', '/')}"],
    )


# ---------------------------------------------------------------- seniority

_LEVEL_ORDER = {lvl: i for i, lvl in enumerate(("intern", "junior", "mid", "senior", "staff", "principal", "director"))}
_LEVEL_ORDER["lead"] = _LEVEL_ORDER["staff"]
_LEVEL_ORDER["manager"] = _LEVEL_ORDER["staff"]


def score_seniority(job: dict, profile: dict) -> Component:
    parts: list[float] = []
    details: list[str] = []
    gaps: list[str] = []
    matches: list[str] = []
    levels = profile.get("seniority_levels") or []
    job_level = job.get("seniority")
    if levels and job_level:
        if job_level in levels:
            parts.append(100)
            matches.append(job_level)
            details.append(f"“{job_level}” is one of your target levels.")
        else:
            distance = min(abs(_LEVEL_ORDER.get(job_level, 2) - _LEVEL_ORDER.get(l, 2)) for l in levels)
            parts.append(60 if distance == 1 else 20)
            details.append(f"Posting is “{job_level}”; you target {', '.join(levels)}.")
            gaps.append(f"Seniority: {job_level}")
    years_have = profile.get("total_experience_years")
    years_need = job.get("experience_min_years")
    if years_have is not None and years_need is not None:
        years_need = float(years_need)
        if years_have >= years_need:
            parts.append(100)
            details.append(f"Asks for {years_need:g}+ years; you have {years_have:g}.")
        else:
            parts.append(70 if years_have >= years_need - 1 else 30)
            details.append(f"Asks for {years_need:g}+ years; you have {years_have:g}.")
            gaps.append(f"Experience: posting asks for {years_need:g}+ years")
    elif years_need is not None:
        details.append(f"Asks for {float(years_need):g}+ years (add your total experience to your profile to compare).")
    if not parts:
        return _unknown(" ".join(details) or "Seniority not stated in the posting or not set in your profile.")
    return Component(sum(parts) / len(parts), " ".join(details), matches, gaps)


# ----------------------------------------------------------------- location


def _location_matches(pref: dict, job_mode: str | None, location: str) -> bool:
    mode = pref.get("mode", "flexible")
    places = [p.lower() for p in pref.get("places", [])]
    if mode == "remote":
        if job_mode != "remote":
            return False
        if not places:  # remote from anywhere: just must not be locked to a far region
            return not _RESTRICTED_REGION_RE.search(location) or bool(_ANYWHERE_RE.search(location))
        if not location or location in ("remote",):
            return False  # can't confirm the region restriction is satisfied
        return any(p in location for p in places) or bool(_INDIA_RE.search(location)) and "india" in places
    allowed_modes = {"onsite": {"onsite"}, "hybrid": {"hybrid"}}.get(mode, {"onsite", "hybrid", "remote"})
    if job_mode not in allowed_modes and job_mode is not None:
        return False
    return any(p in location for p in places) if places else True


def score_location(job: dict, profile: dict) -> Component:
    prefs = profile.get("locations") or []
    if not prefs:
        return _unknown("No location preferences set in your profile.")
    job_mode = job.get("remote_type") or None
    location = (job.get("location") or "").lower().strip()
    if not job_mode and not location:
        return _unknown("The posting doesn't state a location or remote policy.")
    for rank, pref in enumerate(prefs):
        if _location_matches(pref, job_mode, location):
            score = LOCATION_RANK_SCORES[min(rank, len(LOCATION_RANK_SCORES) - 1)]
            return Component(score, f"Matches your #{rank + 1} location preference: {pref['label']}.", [pref["label"]], [])
    where = job.get("location") or job_mode or "unspecified"
    if job_mode == "remote" and location in ("", "remote"):
        return Component(55, "Remote, but the posting doesn't say which regions it hires from.", [], ["Remote region not stated"])
    return Component(15, f"“{where}” is outside your location preferences.", [], [f"Location: {where}"])


# ------------------------------------------------------------- compensation


def score_compensation(job: dict, profile: dict) -> Component:
    comp = profile.get("compensation") or {}
    floor = comp.get("min_annual")
    if not floor:
        return _unknown("No minimum compensation set in your profile.")
    if job.get("salary_max") is None and job.get("salary_min") is None:
        return _unknown("The posting doesn't state compensation.")
    period = job.get("salary_period") or "year"
    high = normalize.annual_amount(float(job.get("salary_max") or job.get("salary_min")), period)
    job_currency = (job.get("salary_currency") or "").upper()
    floor_currency = (comp.get("currency") or "INR").upper()
    estimated = False
    if job_currency and job_currency != floor_currency:
        if job_currency not in APPROX_TO_INR or floor_currency not in APPROX_TO_INR:
            return _unknown(f"Salary is in {job_currency}; not compared against your {floor_currency} floor.")
        high = high * APPROX_TO_INR[job_currency] / APPROX_TO_INR[floor_currency]
        estimated = True
    note = " (estimated using approximate exchange rates)" if estimated else ""
    stated, minimum = _annual_label(high, floor_currency), _annual_label(floor, floor_currency)
    if high >= floor:
        return Component(100, f"Stated pay (up to {stated}) meets your {minimum} minimum{note}.", ["Meets salary floor"], [])
    # A converted (estimated) figure informs the score but isn't trusted
    # enough to rule a job out on its own.
    return Component(
        15, f"Stated pay tops out at {stated}, below your {minimum} minimum{note}.", [],
        [f"Pays below your minimum ({stated} vs {minimum})"], blocking=not estimated,
    )


def _annual_label(amount: float, currency: str) -> str:
    if currency == "INR":
        return f"₹{amount / 100_000:.1f} LPA".replace(".0 LPA", " LPA")
    return f"{amount:,.0f} {currency}/yr"


# ------------------------------------------------------------------- domain


def score_domain(job: dict, profile: dict) -> Component:
    domains = [d for d in profile.get("preferred_domains", []) if d.strip()]
    if not domains:
        return _unknown("No preferred domains set in your profile.")
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    hits = [d for d in domains if d.lower() in text]
    if hits:
        return Component(100, f"Mentions your focus area(s): {', '.join(hits)}.", hits, [])
    return Component(40, "Doesn't mention any of your preferred domains.", [], [])


# -------------------------------------------------------------- combination


def deal_breakers(job: dict, profile: dict) -> list[str]:
    found = []
    company = (job.get("company") or "").lower()
    for excluded in profile.get("excluded_companies", []):
        if excluded and excluded.lower() in company:
            found.append(f"Excluded company: {job.get('company')}")
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    for phrase in profile.get("deal_breakers", []):
        if phrase and phrase.lower() in text:
            found.append(f"Deal-breaker mentioned: “{phrase}”")
    return found


def assess(job: dict, profile: dict, resume_text: str, semantic: dict | None = None) -> dict:
    """semantic: {"score", "rationale", "strengths", "gaps"} from the LLM
    (already validated against the candidate's evidence), or None."""
    weights = {**DEFAULT_WEIGHTS, **(profile.get("weights") or {})}
    candidate_skills = candidate_skill_set(profile, resume_text)
    components: dict[str, Component] = {
        "skills": score_skills(job, candidate_skills, set(profile.get("growth_skills", []))),
        "role": score_role(job, profile),
        "seniority": score_seniority(job, profile),
        "location": score_location(job, profile),
        "compensation": score_compensation(job, profile),
        "domain": score_domain(job, profile),
    }
    if semantic and semantic.get("score") is not None:
        components["semantic"] = Component(
            float(semantic["score"]), semantic.get("rationale") or "",
            list(semantic.get("strengths") or []), list(semantic.get("gaps") or []),
        )
    else:
        components["semantic"] = _unknown("Not analysed by the AI model.")

    known = {k: c for k, c in components.items() if c.score is not None}
    total_weight = sum(weights.values())
    known_weight = sum(weights[k] for k in known)
    overall = sum(weights[k] * c.score for k, c in known.items()) / known_weight if known_weight else 0.0

    blockers = deal_breakers(job, profile)
    if components["compensation"].blocking:
        blockers += components["compensation"].gaps
    if any(b.startswith("Excluded company") for b in blockers):
        overall = 0.0
    elif any(b.startswith("Deal-breaker") for b in blockers):
        overall = min(overall, 25.0)
    elif components["compensation"].blocking:
        overall = min(overall, BELOW_SALARY_FLOOR_CAP)

    strengths: list[str] = []
    skills = components["skills"]
    if skills.matches:
        strengths.append(f"Strong match: {', '.join(skills.matches[:6])}")
    for key in ("role", "location", "compensation", "domain"):
        c = components[key]
        if c.score is not None and c.score >= 80:
            strengths.append(c.detail)
    strengths += [s for s in components["semantic"].matches if s not in strengths][:3]

    gaps: list[str] = list(blockers)
    if skills.gaps:
        gaps.append(
            f"Potential gap: the posting mentions {', '.join(skills.gaps[:5])}, which "
            f"{'is' if len(skills.gaps[:5]) == 1 else 'are'} not documented in your profile or resume."
        )
    for key in ("role", "seniority", "location", "compensation"):
        c = components[key]
        if c.score is not None and c.score < 50 and not c.blocking:
            gaps.append(c.detail)
    gaps += [g for g in components["semantic"].gaps if g not in gaps][:3]

    return {
        "scoring_version": SCORING_VERSION,
        "overall_score": round(overall, 1),
        "confidence": round(known_weight / total_weight, 2) if total_weight else 0,
        "components": {k: c.to_dict(k, weights[k]) for k, c in components.items()},
        "strengths": strengths,
        "gaps": gaps,
        "deal_breakers": blockers,
    }


def location_compatible(job: dict, profile: dict) -> bool:
    """Cheap pre-check used to skip the paid semantic call for postings
    that are plainly outside every location preference."""
    component = score_location(job, profile)
    return component.score is None or component.score >= 50
