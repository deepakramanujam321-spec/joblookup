#!/usr/bin/env python3
"""Weekend digest: everything queued since the last digest, ranked by
priority (fit + freshness + recency + learned preferences), rendered as one
HTML email and sent; those rows are then marked sent. Closed listings and
non-listings are skipped. No LLM call here -- scoring and drafting already
happened in score_and_draft.py; this is pure templating.

Usage:
    python scripts/run_digest.py

Requires DATABASE_URL and SMTP credentials (SMTP_USERNAME/SMTP_PASSWORD or
GMAIL_ADDRESS/GMAIL_APP_PASSWORD). Optional DASHBOARD_URL adds a link per job.
"""

from __future__ import annotations

import html
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sqlalchemy as sa  # noqa: E402
from send_digest import send as send_email  # noqa: E402

from jobseeker import config, drafting, runs  # noqa: E402
from jobseeker.database import get_engine, jobs  # noqa: E402
from jobseeker.profile import DEFAULT_OWNER  # noqa: E402

CARD_TEMPLATE = """
<div style="border:1px solid #e3e5e8;border-radius:8px;padding:16px;margin-bottom:14px;">
  <div style="font-size:12px;color:#6b7280;">Fit {fit_score:.0f}/100 &middot; priority {priority:.0f} &middot; {source} &middot; {posted}</div>
  <h3 style="margin:4px 0 2px;"><a href="{url}" style="color:#1a56db;text-decoration:none;">{title}</a></h3>
  <div style="color:#374151;margin-bottom:8px;">{company} &middot; {location}</div>
  <div style="color:#4b5563;margin-bottom:10px;">{fit_rationale}</div>
  {draft_block}
  {dashboard_link}
</div>
"""


def _posted_label(job: dict) -> str:
    ts = job.get("posted_at_ts")
    if ts is None:
        return "posting date unavailable"
    days = (datetime.now(timezone.utc) - ts).days
    return "posted today" if days < 1 else f"posted {days} day{'s' if days != 1 else ''} ago"


def render_digest(rows: list[dict], dashboard_url: str | None) -> str:
    cards = []
    for j in rows:
        draft = j.get("draft_body") or ""
        draft_block = (
            f'<div style="background:#f6f7f9;border-radius:6px;padding:12px;white-space:pre-wrap;font-size:14px;">{html.escape(draft)}</div>'
            if draft else ""
        )
        link = (
            f'<div style="margin-top:10px;font-size:13px;"><a href="{html.escape(dashboard_url.rstrip("/"))}/jobs/{j["id"]}">Open in JobLookup</a></div>'
            if dashboard_url else ""
        )
        cards.append(CARD_TEMPLATE.format(
            fit_score=float(j.get("fit_score") or 0), priority=float(j.get("priority_score") or j.get("fit_score") or 0),
            source=html.escape(j.get("source", "")), posted=_posted_label(j), url=html.escape(j["url"]),
            title=html.escape(j["title"]), company=html.escape(j.get("company") or ""),
            location=html.escape(j.get("location") or "location not stated"),
            fit_rationale=html.escape(j.get("fit_rationale") or ""), draft_block=draft_block, dashboard_link=link,
        ))
    n = len(rows)
    return f"""<html><body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:640px;margin:0 auto;color:#111827;">
<h2>{n} new match{'es' if n != 1 else ''} this week</h2>
<p style="color:#6b7280;">Ranked by priority (fit, freshness, recency and your feedback). Drafts are suggestions to
review and send yourself &mdash; nothing has been sent on your behalf.</p>
{''.join(cards)}
</body></html>"""


def main() -> int:
    profile = config.load_profile()
    engine = get_engine()
    dashboard_url = os.environ.get("DASHBOARD_URL")
    with runs.recorded(engine, "digest") as rec:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                sa.select(jobs).where(
                    jobs.c.status == "queued_for_digest", jobs.c.verification_status != "closed",
                    jobs.c.listing_quality == "ok",
                ).order_by(jobs.c.priority_score.desc().nullslast(), jobs.c.fit_score.desc().nullslast())
            ).mappings()]
            for row in rows:
                latest = drafting.latest_version(conn, DEFAULT_OWNER, row["id"])
                row["draft_body"] = latest.body if latest else row.get("outreach_draft")
        rec.values["jobs_found"] = len(rows)
        if not rows:
            print("[run_digest] nothing queued, skipping send", file=sys.stderr)
            return 0

        today = date.today().isoformat()
        from_addr = config.require_env_any(["SMTP_USERNAME", "GMAIL_ADDRESS"])
        app_password = config.require_env_any(["SMTP_PASSWORD", "GMAIL_APP_PASSWORD"])
        to_addr = profile["candidate"]["digest_email"]
        subject = f"Job digest — {len(rows)} new match{'es' if len(rows) != 1 else ''} — {today}"
        send_email(render_digest(rows, dashboard_url), subject, to_addr, from_addr, app_password)
        with engine.begin() as conn:
            conn.execute(sa.update(jobs).where(jobs.c.id.in_([r["id"] for r in rows])).values(
                status="sent_in_digest", digest_batch_date=today))
        rec.values["jobs_new"] = len(rows)
        print(f"[run_digest] sent: {len(rows)} job(s) to {to_addr}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
