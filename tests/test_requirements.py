"""The pipeline (GitHub Actions) installs only requirements.txt, so every
third-party module the pipeline scripts can import must be listed there.
A dev environment with extra packages hides exactly this kind of gap (it
broke the first resume-hub sync: pypdf was missing)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# module imported by pipeline code -> distribution name in requirements.txt
PIPELINE_MODULES = {
    "pypdf": "pypdf", "docx": "python-docx", "nh3": "nh3", "bs4": "beautifulsoup4", "feedparser": "feedparser",
    "litellm": "litellm", "sqlalchemy": "sqlalchemy", "psycopg": "psycopg", "pydantic": "pydantic",
    "requests": "requests", "yaml": "pyyaml", "scrapling": "scrapling",
}


def _declared(path: Path) -> set[str]:
    names = set()
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-r"):
            names.add(re.split(r"[\[<>=~! ]", line, maxsplit=1)[0].lower())
    return names


def _imported_by_pipeline() -> set[str]:
    found = set()
    for folder in ("src/jobseeker", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for module in PIPELINE_MODULES:
                if re.search(rf"^\s*(import {module}\b|from {module}[\s.])", text, re.M):
                    found.add(module)
    return found


def test_pipeline_requirements_cover_pipeline_imports():
    declared = _declared(ROOT / "requirements.txt")
    missing = {PIPELINE_MODULES[m] for m in _imported_by_pipeline()} - declared
    assert not missing, f"requirements.txt is missing {sorted(missing)}"
