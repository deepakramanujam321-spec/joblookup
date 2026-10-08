#!/usr/bin/env python3
"""Mechanical send step: takes a pre-built HTML file and emails it over SMTP.
Does no composing, no filtering, no DB access — the calling Routine (see
README.md) builds the digest content and passes it in. Keeping this script
dumb means the one step with real-world side effects (an email leaving the
system) is also the simplest one to audit.

Defaults to Gmail's SMTP server, but any provider works: set SMTP_HOST (and
SMTP_PORT if not 465) to point elsewhere. SMTP_USERNAME/SMTP_PASSWORD are
the generic names; GMAIL_ADDRESS/GMAIL_APP_PASSWORD work too, as a
convenience alias for the common case.

Usage:
    python scripts/send_digest.py --html-file digest.html --subject "..." --to someone@example.com
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import config

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465


def send(html_body: str, subject: str, to_addr: str, from_addr: str, password: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html"))

    host = os.environ.get("SMTP_HOST") or DEFAULT_SMTP_HOST
    port = int(os.environ.get("SMTP_PORT") or DEFAULT_SMTP_PORT)
    with smtplib.SMTP_SSL(host, port) as server:
        server.login(from_addr, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-file", required=True, type=Path)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--to", required=False, help="defaults to candidate.digest_email in profile.yaml")
    args = parser.parse_args()

    if not args.html_file.exists():
        print(f"[send_digest] no such file: {args.html_file}", file=sys.stderr)
        return 1

    profile = config.load_profile()
    to_addr = args.to or profile["candidate"]["digest_email"]
    from_addr = config.require_env_any(["SMTP_USERNAME", "GMAIL_ADDRESS"])
    password = config.require_env_any(["SMTP_PASSWORD", "GMAIL_APP_PASSWORD"])

    html_body = args.html_file.read_text(encoding="utf-8")
    send(html_body, args.subject, to_addr, from_addr, password)
    print(f"[send_digest] sent to {to_addr}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
