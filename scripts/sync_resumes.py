#!/usr/bin/env python3
"""Pulls new/changed resumes from the Drive folder set on the profile
("resume hub"). Runs before scoring so scoring and drafting use the latest
resume. A Drive hiccup is logged, never fatal: existing resumes keep working.

Usage: python scripts/sync_resumes.py
Requires DATABASE_URL.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import profile, resume_hub  # noqa: E402
from jobseeker.database import get_engine  # noqa: E402


def main() -> int:
    engine = get_engine()
    with engine.begin() as conn:
        record = profile.get_or_seed(conn, profile.DEFAULT_OWNER)
        url = record["data"].get("resume_folder_url")
        if not url:
            print("[sync_resumes] no resume folder set on the profile; skipping", file=sys.stderr)
            return 0
        try:
            result = resume_hub.sync(conn, profile.DEFAULT_OWNER, url)
        except Exception as e:
            print(f"[sync_resumes] sync failed, keeping existing resumes: {type(e).__name__}: {e}", file=sys.stderr)
            return 0
    print(f"[sync_resumes] {result.as_dict()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
