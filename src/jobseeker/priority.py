"""Application priority: what deserves attention first.

Kept separate from fit on purpose. Fit answers "how well does this match
me?"; recency answers "how new is the posting?"; freshness answers "how
sure are we it's still open?"; priority combines them (plus learned
preferences) into an ordering, and every adjustment is recorded with its
reason so the UI can show exactly why a job ranks where it does.
"""

from __future__ import annotations

from datetime import datetime, timezone

MAX_LEARNED_ADJUSTMENT = 12.0


def recency(posted_at: datetime | None, now: datetime) -> dict:
    if posted_at is None:
        return {"state": "unknown", "age_days": None}
    age = (now - posted_at).total_seconds() / 86400
    if age <= 3:
        state = "new"
    elif age <= 14:
        state = "recent"
    elif age <= 45:
        state = "aging"
    else:
        state = "old"
    return {"state": state, "age_days": round(max(age, 0), 1)}


def freshness(job: dict, now: datetime, stale_days: int) -> dict:
    """verified | unchecked | possibly_stale | closed | not_a_listing"""
    if job.get("listing_quality") == "not_a_listing":
        return {"state": "not_a_listing", "detail": "Not an individual job posting"}
    status = job.get("verification_status") or "unchecked"
    if status == "closed":
        return {"state": "closed", "detail": "The source reports this posting is closed"}
    verified = job.get("last_verified_at")
    if verified is not None:
        age = (now - verified).total_seconds() / 86400
        if age <= stale_days and status == "active":
            return {"state": "verified", "detail": f"Confirmed open {age:.0f} day(s) ago" if age >= 1 else "Confirmed open today"}
        return {"state": "possibly_stale", "detail": f"Last confirmed open {age:.0f} days ago"}
    if status in ("verification_failed", "source_unavailable"):
        return {"state": "possibly_stale", "detail": "Couldn't reach the source to confirm it's open"}
    seen = job.get("last_seen_at") or job.get("discovered_at")
    if seen is not None and (now - seen).total_seconds() / 86400 > stale_days:
        return {"state": "possibly_stale", "detail": "Not seen in collection or verified recently"}
    return {"state": "unchecked", "detail": "Not verified yet"}


def learned_adjustment(signals: dict[str, list[str]], learned: list[dict]) -> tuple[float, list[dict]]:
    """signals: dimension -> this job's values (see learning.job_signals).
    learned: active learned preferences. Returns (capped total, reasons)."""
    reasons = []
    total = 0.0
    for pref in learned:
        if not pref.get("active") or pref.get("disabled_by_user"):
            continue
        if pref["value"] in signals.get(pref["dimension"], []):
            delta = float(pref["weight"])
            total += delta
            reasons.append({"label": pref["explanation"] or f"Learned: {pref['dimension']} {pref['value']}", "delta": round(delta, 1)})
    capped = max(-MAX_LEARNED_ADJUSTMENT, min(MAX_LEARNED_ADJUSTMENT, total))
    return capped, reasons


def compute(job: dict, fit: float | None, signals: dict, learned: list[dict], stale_days: int, now: datetime | None = None) -> tuple[float | None, dict]:
    now = now or datetime.now(timezone.utc)
    if fit is None:
        return None, {"reasons": [], "recency": recency(job.get("posted_at_ts"), now), "freshness": freshness(job, now, stale_days)}
    reasons: list[dict] = [{"label": "Candidate fit", "delta": round(fit, 1)}]
    score = fit
    fresh = freshness(job, now, stale_days)
    rec = recency(job.get("posted_at_ts"), now)
    if fresh["state"] in ("closed", "not_a_listing"):
        reasons.append({"label": fresh["detail"], "delta": round(-score, 1)})
        return 0.0, {"reasons": reasons, "recency": rec, "freshness": fresh}
    if fresh["state"] == "verified":
        score += 2
        reasons.append({"label": "Recently confirmed open", "delta": 2})
    elif fresh["state"] == "possibly_stale":
        score -= 5
        reasons.append({"label": fresh["detail"], "delta": -5})
    recency_delta = {"new": 6, "recent": 3, "aging": 0, "old": -6}.get(rec["state"], 0)
    if recency_delta:
        label = {"new": "Posted in the last 3 days", "recent": "Posted in the last 2 weeks", "old": "Posted over 45 days ago"}[rec["state"]]
        reasons.append({"label": label, "delta": recency_delta})
        score += recency_delta
    adj, learned_reasons = learned_adjustment(signals, learned)
    if learned_reasons:
        score += adj
        reasons += learned_reasons
        if adj != sum(r["delta"] for r in learned_reasons):
            reasons.append({"label": f"Learned adjustments capped at ±{MAX_LEARNED_ADJUSTMENT:g}", "delta": 0})
    return round(max(0.0, min(100.0, score)), 1), {"reasons": reasons, "recency": rec, "freshness": fresh}
