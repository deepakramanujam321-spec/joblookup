#!/usr/bin/env python3
"""Scores every newly-collected job against the resume, and drafts an
outreach note for the strong matches. This is the one step in the pipeline
that calls an LLM — everything else (scraping, filtering, sending) is
plain deterministic code.

Provider-agnostic via LiteLLM: swap LLM_MODEL to any provider/model LiteLLM
supports ("anthropic/claude-sonnet-5", "openai/gpt-4o-mini",
"gemini/gemini-2.0-flash", "groq/llama-3.3-70b-versatile", ...) and set that
provider's API key env var — no code changes. See README.md for the
model-string-to-env-var mapping.

Usage:
    python scripts/run_collect.py > jobs.json
    python scripts/score_and_draft.py --jobs-file jobs.json

Requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, LLM_MODEL (optional,
defaults below), and whichever API key env var LLM_MODEL's provider needs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import litellm

from jobseeker import config
from jobseeker.models import JobListing
from jobseeker.storage import SupabaseStore

DEFAULT_MODEL = "anthropic/claude-sonnet-5"
FIT_SCORE_THRESHOLD = 70
MAX_JOBS_PER_RUN = 50  # cost/time ceiling in case a run surfaces an unusual flood of results

ASSESSMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_job_assessment",
        "description": "Submit the fit assessment for this job posting.",
        "parameters": {
            "type": "object",
            "properties": {
                "fit_score": {
                    "type": "number",
                    "description": "0-100 fit score for this specific candidate and this specific posting.",
                },
                "fit_rationale": {
                    "type": "string",
                    "description": "1-2 sentences, specific to this posting (not generic boilerplate).",
                },
                "outreach_draft": {
                    "type": ["string", "null"],
                    "description": (
                        "A short outreach/application note in the candidate's voice, 3-5 sentences: "
                        "why this role fits the candidate's experience (per the resume below), one "
                        "concrete anchor story from the resume relevant to THIS posting, a clear ask "
                        "to move forward. Required if fit_score >= 70, otherwise null."
                    ),
                },
            },
            "required": ["fit_score", "fit_rationale", "outreach_draft"],
        },
    },
}


def build_prompt(job: dict, resume_text: str, profile: dict) -> str:
    location_pref = profile["location_preference"]
    salary_floor = profile["salary_floor_lpa"]
    return f"""You are assessing a job posting's fit for a specific candidate, for a job-search
assistant that scores postings so only strong matches reach the candidate's weekly digest.

CANDIDATE RESUME:
{resume_text}

CANDIDATE'S STATED PRIORITIES:
- Location preference, most to least preferred: {", ".join(location_pref)}
- Salary floor: {salary_floor} LPA (only penalize if the posting states a lower figure explicitly)

JOB POSTING:
Title: {job['title']}
Company: {job['company']}
Location: {job.get('location') or 'not stated'}
Remote type: {job.get('remote_type') or 'not stated'}
Salary: {job.get('salary_text') or 'not stated'}
Source: {job['source']}
URL: {job['url']}

Description:
{(job.get('description') or '')[:3000]}

Score this posting's fit for this candidate, 0-100, based on the ACTUAL experience and
skills in the resume above — don't invent generic strengths not demonstrated there. Weigh
location against the candidate's stated priority order and apply the salary floor only when
a figure is actually given. Call submit_job_assessment with your result."""


def assess_job(model: str, job: dict, resume_text: str, profile: dict) -> dict:
    resp = litellm.completion(
        model=model,
        max_tokens=1024,
        tools=[ASSESSMENT_TOOL],
        tool_choice={"type": "function", "function": {"name": "submit_job_assessment"}},
        messages=[{"role": "user", "content": build_prompt(job, resume_text, profile)}],
    )
    tool_call = resp.choices[0].message.tool_calls[0]
    return json.loads(tool_call.function.arguments)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs-file", required=True, help="JSON output of run_collect.py")
    args = parser.parse_args()

    profile = config.load_profile()
    resume_text = config.load_resume_text(profile)
    # `or` (not .get's default arg) because GitHub Actions sets an unset
    # secret's env var to an empty string rather than omitting it.
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL

    supabase_url = config.require_env("SUPABASE_URL")
    supabase_key = config.require_env("SUPABASE_SERVICE_ROLE_KEY")
    store = SupabaseStore(supabase_url, supabase_key)

    raw = json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
    listings = [JobListing(**row) for row in raw]
    inserted = store.insert_new_jobs(listings)
    print(f"[score_and_draft] {len(listings)} candidates found, {inserted} new", file=sys.stderr)

    pending = store.list_jobs("new")[:MAX_JOBS_PER_RUN]
    print(f"[score_and_draft] scoring {len(pending)} pending job(s) with {model}...", file=sys.stderr)

    queued_count = 0
    error = ""
    try:
        for job in pending:
            assessment = assess_job(model, job, resume_text, profile)
            fit_score = assessment["fit_score"]
            fields = {
                "fit_score": fit_score,
                "fit_rationale": assessment["fit_rationale"],
            }
            if fit_score >= FIT_SCORE_THRESHOLD:
                fields["status"] = "queued_for_digest"
                fields["outreach_draft"] = assessment["outreach_draft"]
                queued_count += 1
            else:
                fields["status"] = "scored"
            store.update_job(job["id"], fields)
            time.sleep(0.5)  # stay comfortably under rate limits
    except Exception as e:
        error = str(e)
        print(f"[score_and_draft] stopped early: {error}", file=sys.stderr)

    store.log_run("collect", jobs_found=len(listings), jobs_new=inserted, error=error)
    print(
        f"[score_and_draft] done: {len(listings)} found, {inserted} new, {queued_count} queued for digest",
        file=sys.stderr,
    )
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
