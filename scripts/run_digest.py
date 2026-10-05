#!/usr/bin/env python3
"""Weekend digest: pulls everything queued since the last digest, renders one
HTML email ranked by fit, sends it, and marks those rows sent. No LLM call
here — scoring and drafting already happened in score_and_draft.py, this
step is pure deterministic rendering of already-computed data.

Usage:
    python scripts/run_digest.py

Requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD.
"""

from __future__ import annotations

import html
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from send_digest import send as send_email  # noqa: E402

from jobseeker import config  # noqa: E402
from jobseeker.storage import SupabaseStore  # noqa: E402

CARD_TEMPLATE = """
<div style="border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:16px;">
  <div style="font-size:12px;color:#888;">Fit score: {fit_score:.0f}/100 &middot; {source}</div>
  <h3 style="margin:4px 0;"><a href="{url}" style="color:#1a73e8;text-decoration:none;">{title}</a></h3>
  <div style="color:#333;margin-bottom:8px;">{company} &middot; {location}</div>
  <div style="font-style:italic;color:#555;margin-bottom:12px;">{fit_rationale}</div>
  <div style="background:#f6f6f6;border-radius:6px;padding:12px;white-space:pre-wrap;">{outreach_draft}</div>
</div>
"""


def render_digest(jobs: list[dict]) -> str:
    jobs_sorted = sorted(jobs, key=lambda j: j.get("fit_score") or 0, reverse=True)
    cards = "".join(
        CARD_TEMPLATE.format(
            fit_score=j.get("fit_score") or 0,
            source=html.escape(j.get("source", "")),
            url=html.escape(j["url"]),
            title=html.escape(j["title"]),
            company=html.escape(j.get("company", "")),
            location=html.escape(j.get("location") or "not stated"),
            fit_rationale=html.escape(j.get("fit_rationale") or ""),
            outreach_draft=html.escape(j.get("outreach_draft") or ""),
        )
        for j in jobs_sorted
    )
    return f"""<html><body style="font-family:-apple-system,Arial,sans-serif;max-width:640px;margin:0 auto;">
<h2>{len(jobs)} new match{'es' if len(jobs) != 1 else ''} this week</h2>
<p style="color:#666;">Ranked by fit. Each card's shaded block is a drafted note you can copy,
tweak, and send yourself — nothing here has been sent anywhere on your behalf.</p>
{cards}
</body></html>"""


def main() -> int:
    profile = config.load_profile()
    supabase_url = config.require_env("SUPABASE_URL")
    supabase_key = config.require_env("SUPABASE_SERVICE_ROLE_KEY")
    store = SupabaseStore(supabase_url, supabase_key)

    jobs = store.list_jobs("queued_for_digest")

    if not jobs:
        print("[run_digest] nothing queued, skipping send", file=sys.stderr)
        store.log_run("digest", jobs_found=0, jobs_new=0)
        return 0

    html_body = render_digest(jobs)
    today = date.today().isoformat()

    from_addr = config.require_env("GMAIL_ADDRESS")
    app_password = config.require_env("GMAIL_APP_PASSWORD")
    to_addr = profile["candidate"]["digest_email"]
    subject = f"Job digest — {len(jobs)} new match{'es' if len(jobs) != 1 else ''} — {today}"

    error = ""
    try:
        send_email(html_body, subject, to_addr, from_addr, app_password)
        for job in jobs:
            store.update_job(job["id"], {"status": "sent_in_digest", "digest_batch_date": today})
    except Exception as e:
        error = str(e)
        print(f"[run_digest] send failed: {error}", file=sys.stderr)

    store.log_run("digest", jobs_found=len(jobs), jobs_new=len(jobs), error=error)
    print(f"[run_digest] {'sent' if not error else 'FAILED'}: {len(jobs)} job(s) to {to_addr}", file=sys.stderr)
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
