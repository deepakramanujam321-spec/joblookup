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


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"See .env.example for what's needed and README.md for where to set it."
        )
    return value
