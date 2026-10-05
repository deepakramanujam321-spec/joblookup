#!/usr/bin/env python3
"""Mechanical send step: takes a pre-built HTML file and emails it via Gmail
SMTP. Does no composing, no filtering, no DB access — the calling Routine
(see README.md) builds the digest content and passes it in. Keeping this
script dumb means the one step with real-world side effects (an email
leaving the system) is also the simplest one to audit.

Usage:
    python scripts/send_digest.py --html-file digest.html --subject "..." --to someone@example.com
"""

from __future__ import annotations

import argparse
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobseeker import config

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def send(html_body: str, subject: str, to_addr: str, from_addr: str, app_password: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(from_addr, app_password)
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
    from_addr = config.require_env("GMAIL_ADDRESS")
    app_password = config.require_env("GMAIL_APP_PASSWORD")

    html_body = args.html_file.read_text(encoding="utf-8")
    send(html_body, args.subject, to_addr, from_addr, app_password)
    print(f"[send_digest] sent to {to_addr}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
