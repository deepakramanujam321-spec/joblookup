"""Turns structured feedback into ranking signals -- transparently.

Each feedback category says something about *specific* attributes of the
job it was given on (its seniority, its role family, the technologies it
asks for that you don't have, its location...). Those attributes are the
only things that feedback can move, which is what keeps "wrong seniority"
on one posting from leaking into unrelated categories.

Safeguards, in code rather than in promises:
  * MIN_EVIDENCE: nothing becomes active from a single data point.
  * Explicit beats inferred: a learned negative on something your profile
    explicitly asks for (a target seniority, a listed skill, a preferred
    work mode) is kept visible but inactive, with the reason.
  * Bounded: per-preference weights are capped, and priority.py caps the
    total learned adjustment per job.
  * Inspectable/resettable: preferences are stored with their counts and
    explanation; the user can disable any one, or reset learning (which
    ignores feedback before the reset time -- the feedback itself is kept).
  * Salary/duplicate/inaccurate feedback is recorded but teaches nothing:
    salary is an explicit preference, the others are data-quality reports.

evaluate() measures whether the learned adjustments actually rank liked
jobs above disliked ones better than the unadjusted score, using
leave-one-out so a job's own feedback never helps score that same job.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from . import normalize

MIN_EVIDENCE = 2
MAX_WEIGHT = {"category": 10.0, "seniority": 8.0, "skill": 3.0, "work_mode": 8.0, "company": 10.0, "region": 6.0}

# category -> list of (dimension, polarity, strength)
FEEDBACK_EFFECTS: dict[str, list[tuple[str, int, float]]] = {
    "excellent_match": [("category", +1, 1.0), ("seniority", +1, 1.0), ("matched_skill", +1, 1.0), ("work_mode", +1, 0.5)],
    "relevant_not_priority": [("category", -1, 0.5)],
    "wrong_seniority": [("seniority", -1, 1.0)],
    "experience_mismatch": [("seniority", -1, 1.0)],
    "wrong_stack": [("gap_skill", -1, 1.0)],
    "wrong_category": [("category", -1, 1.0)],
    "not_interested": [("category", -1, 0.5)],
    "location_mismatch": [("work_mode", -1, 0.5), ("region", -1, 1.0)],
    "company_mismatch": [("company", -1, 1.0)],
    "salary_mismatch": [],
    "duplicate": [],
    "inaccurate_listing": [],
    "other": [],
}
# Application progress is an explicit positive action, so it counts too.
APPLICATION_POSITIVE_STATUSES = {"shortlisted", "draft_ready", "applied", "recruiter_response", "interview", "offer"}

POSITIVE_FEEDBACK = {"excellent_match"}
NEGATIVE_FEEDBACK = {"wrong_seniority", "wrong_stack", "wrong_category", "not_interested", "location_mismatch",
                     "company_mismatch", "experience_mismatch"}


def region_of(location: str | None) -> str | None:
    loc = (location or "").lower()
    if not loc:
        return None
    for region, terms in (
        ("india", ("india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai", "chennai", "delhi", "noida", "gurgaon")),
        ("north_america", ("us", "usa", "united states", "canada", "new york", "san francisco", "seattle", "toronto")),
        ("europe", ("europe", "emea", "uk", "london", "berlin", "germany", "france", "paris", "amsterdam", "dublin", "spain", "poland")),
        ("apac", ("apac", "singapore", "australia", "japan", "tokyo", "sydney")),
        ("latam", ("latam", "brazil", "mexico", "argentina")),
    ):
        if any(f" {t} " in f" {loc.replace(',', ' ')} " for t in terms):
            return region
    return None


def job_signals(job: dict, candidate_skills: set[str]) -> dict[str, list[str]]:
    lowered = {s.lower() for s in candidate_skills}
    skills = job.get("skills") or []
    region = region_of(job.get("location"))
    return {
        "category": [job.get("job_category") or normalize.detect_category(job.get("title"))],
        "seniority": [job["seniority"]] if job.get("seniority") else [],
        "matched_skill": [s for s in skills if s.lower() in lowered],
        "gap_skill": [s for s in skills if s.lower() not in lowered and not normalize.is_generic_skill(s)],
        "work_mode": [job["remote_type"]] if job.get("remote_type") else [],
        "region": [region] if region else [],
        "company": [(job.get("company") or "").strip().lower()] if job.get("company") else [],
    }


def ranking_signals(signals: dict[str, list[str]]) -> dict[str, list[str]]:
    """Signals as priority.py matches them: matched/gap skills both map to
    the single learned 'skill' dimension."""
    out = {k: v for k, v in signals.items() if k not in ("matched_skill", "gap_skill")}
    out["skill"] = signals.get("matched_skill", []) + signals.get("gap_skill", [])
    return out


def _explicit_preferences(profile: dict, candidate_skills: set[str]) -> dict[str, set[str]]:
    work_modes = {p.get("mode") for p in profile.get("locations", [])}
    if "flexible" in work_modes:
        work_modes |= {"onsite", "hybrid"}
    from .matching import preferred_categories  # local import: matching imports normalize only

    return {
        "seniority": set(profile.get("seniority_levels") or []),
        "category": preferred_categories(profile),
        "skill": {s.lower() for s in candidate_skills} | {g.lower() for g in profile.get("growth_skills", [])},
        "work_mode": {m for m in work_modes if m},
        "company": set(),
        "region": {"india"} if any("india" in (p.get("places") or []) for p in profile.get("locations", [])) else set(),
    }


def compute_preferences(events: list[dict], profile: dict, candidate_skills: set[str], overrides: dict[tuple[str, str], bool] | None = None) -> list[dict]:
    """events: [{"kind": "feedback"|"application", "category"/"status", "signals": {...}}]
    Returns learned preference rows (active or not), deterministic for the
    same input."""
    overrides = overrides or {}
    tallies: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for event in events:
        if event["kind"] == "feedback":
            effects = FEEDBACK_EFFECTS.get(event["category"], [])
        elif event["status"] in APPLICATION_POSITIVE_STATUSES:
            effects = [("category", +1, 1.0), ("seniority", +1, 0.5)]
        else:
            effects = []
        for signal_dim, polarity, strength in effects:
            dimension = "skill" if signal_dim in ("matched_skill", "gap_skill") else signal_dim
            for value in event["signals"].get(signal_dim, []):
                tallies[(dimension, value)][0 if polarity > 0 else 1] += strength

    explicit = _explicit_preferences(profile, candidate_skills)
    rows = []
    for (dimension, value), (pos, neg) in sorted(tallies.items()):
        evidence = pos + neg
        weight = MAX_WEIGHT.get(dimension, 5.0) * (pos - neg) / (evidence + 2)
        active = max(pos, neg) >= MIN_EVIDENCE and abs(weight) >= 1.0
        pretty = value.replace("_", "/") if dimension == "category" else value
        direction = "more" if weight > 0 else "less"
        explanation = f"You've marked {pos:g} positive / {neg:g} negative on {dimension.replace('_', ' ')} “{pretty}”, so similar jobs rank {direction} highly."
        if active and weight < 0 and value.lower() in {v.lower() for v in explicit.get(dimension, set())}:
            active = False
            explanation = f"Feedback leans against {dimension.replace('_', ' ')} “{pretty}”, but your profile explicitly asks for it, so your profile wins. Update your profile if that's changed."
        elif not active and evidence < MIN_EVIDENCE:
            explanation = f"Only {evidence:g} signal(s) on {dimension.replace('_', ' ')} “{pretty}” so far; needs {MIN_EVIDENCE} before it affects ranking."
        disabled = overrides.get((dimension, value), False)
        rows.append({
            "dimension": dimension, "value": value, "weight": round(weight, 2),
            "positive": int(round(pos)), "negative": int(round(neg)),
            "active": active, "disabled_by_user": disabled, "explanation": explanation,
        })
    return rows


def _auc(scored: list[tuple[float, int]]) -> float | None:
    positives = [s for s, label in scored if label == 1]
    negatives = [s for s, label in scored if label == 0]
    if not positives or not negatives:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def evaluate(labelled: list[dict], profile: dict, candidate_skills: set[str]) -> dict:
    """labelled: [{"job_id", "label": 1|0, "base_score", "signals", "event"}].
    AUC: probability a liked job outranks a disliked one (0.5 = chance)."""
    from .priority import learned_adjustment

    if len({row["label"] for row in labelled}) < 2 or len(labelled) < 4:
        return {"status": "insufficient_data", "labelled": len(labelled),
                "message": "Give feedback on at least 4 jobs, including both good and bad matches, to measure ranking quality."}
    base, adjusted = [], []
    for row in labelled:
        others = [r["event"] for r in labelled if r["job_id"] != row["job_id"]]
        prefs = [p for p in compute_preferences(others, profile, candidate_skills) if p["active"]]
        delta, _ = learned_adjustment(ranking_signals(row["signals"]), prefs)
        base.append((row["base_score"], row["label"]))
        adjusted.append((row["base_score"] + delta, row["label"]))
    auc_base, auc_adjusted = _auc(base), _auc(adjusted)
    return {
        "status": "ok", "labelled": len(labelled),
        "auc_without_learning": round(auc_base, 3), "auc_with_learning": round(auc_adjusted, 3),
        "improvement": round(auc_adjusted - auc_base, 3),
        "message": (
            "Learned preferences rank your liked jobs above disliked ones more often than fit alone."
            if auc_adjusted > auc_base + 0.01 else
            "Learned preferences aren't measurably improving ranking yet."
            if abs(auc_adjusted - auc_base) <= 0.01 else
            "Learned preferences are currently ranking worse than fit alone; consider resetting them."
        ),
    }


def now() -> datetime:
    return datetime.now(timezone.utc)
