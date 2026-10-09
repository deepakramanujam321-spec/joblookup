"""Loads config/profile.yaml into plain dicts. No framework, no magic."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = REPO_ROOT / "config" / "profile.yaml"


def load_profile(path: Path = DEFAULT_PROFILE_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_resume_text(profile: dict) -> str:
    resume_path = REPO_ROOT / profile["candidate"]["resume_file"]
    return resume_path.read_text(encoding="utf-8")


# The complete list of environment variables this repo reads, one name per
# concept. tests/test_env_registry.py fails if code reads a name that isn't
# here, or if an entry here is no longer read anywhere. Before adding one:
# check this list for an existing variable that already means the same
# thing (same credential, same URL, same service) and reuse it instead.
ENV_VARS: dict[str, str] = {
    # core
    "DATABASE_URL": "Postgres connection string (Supabase Session pooler). The only database setting.",
    "DASHBOARD_USERNAME": "HTTP Basic Auth username for the dashboard.",
    "DASHBOARD_PASSWORD": "HTTP Basic Auth password for the dashboard.",
    "DASHBOARD_URL": "Public URL of the dashboard (digest links, OAuth redirect off Render).",
    "RENDER_EXTERNAL_URL": "Set by Render automatically; fallback for DASHBOARD_URL.",
    "DASHBOARD_ACCOUNT_ID": "Optional: account id owning user data (default 'default').",
    "DB_POOL_SIZE": "Optional: connection pool size (default 3).",
    # collection + LLM (LiteLLM: one provider key is enough)
    "BRAVE_API_KEY": "Brave Search API key for discovery.",
    "LLM_MODEL": "Optional: explicit LiteLLM model string; otherwise auto-detected from the key below.",
    "OPENAI_API_KEY": "LLM provider key (any one of these four).",
    "ANTHROPIC_API_KEY": "LLM provider key (any one of these four).",
    "GEMINI_API_KEY": "LLM provider key (any one of these four).",
    "GROQ_API_KEY": "LLM provider key (any one of these four).",
    "MAX_LLM_CALLS_PER_RUN": "Optional: cost cap on semantic scoring calls per run (default 60).",
    # digest email
    "GMAIL_ADDRESS": "Sender address for the digest (legacy alias of SMTP_USERNAME).",
    "GMAIL_APP_PASSWORD": "Sender app password (legacy alias of SMTP_PASSWORD).",
    "SMTP_USERNAME": "Sender address for the digest.",
    "SMTP_PASSWORD": "Sender password for the digest.",
    "SMTP_HOST": "Optional: SMTP server (default smtp.gmail.com).",
    "SMTP_PORT": "Optional: SMTP port (default 465).",
    # optional Google integrations
    "GOOGLE_CLIENT_ID": "Google OAuth client id.",
    "GOOGLE_CLIENT_SECRET": "Google OAuth client secret.",
    "GOOGLE_API_KEY": "Browser API key for the Drive Picker.",
    "GOOGLE_APP_ID": "Google Cloud project number (Drive Picker).",
    "TOKEN_ENCRYPTION_KEY": "Encrypts stored Google tokens.",
    # tests only
    "TEST_DATABASE_URL": "Admin URL of a disposable Postgres for the test suite.",
}


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"See .env.example for what's needed and README.md for where to set it."
        )
    return value


def require_env_any(names: list[str]) -> str:
    """Like require_env, but tries several names in order -- for a setting
    with a generic name (SMTP_USERNAME) and a legacy/convenience alias
    (GMAIL_ADDRESS), without forcing every deployment to set both."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError(
        f"Missing required environment variable: set one of {names}. "
        f"See .env.example for what's needed and README.md for where to set it."
    )
