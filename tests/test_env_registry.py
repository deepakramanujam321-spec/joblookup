"""Guards against environment-variable sprawl: every variable the code,
workflows or render.yaml reference must be registered (once, with its
purpose) in jobseeker.config.ENV_VARS, and every registered variable must
still be used. Adding a variable means reading that list first."""

import re
from pathlib import Path

from jobseeker.config import ENV_VARS
from jobseeker.llm import PROVIDER_AUTODETECT

ROOT = Path(__file__).resolve().parents[1]
CODE_PATTERNS = [
    re.compile(r"""environ\.get\(\s*["']([A-Z][A-Z0-9_]+)["']"""),
    re.compile(r"""require_env\(\s*["']([A-Z][A-Z0-9_]+)["']"""),
    re.compile(r"""setenv\(\s*["']([A-Z][A-Z0-9_]+)["']"""),
]
LIST_PATTERN = re.compile(r"require_env_any\(\s*\[([^\]]*)\]")
WORKFLOW_PATTERN = re.compile(r"secrets\.([A-Z][A-Z0-9_]+)")
RENDER_PATTERN = re.compile(r"-\s*key:\s*([A-Z][A-Z0-9_]+)")


def _python_sources():
    for folder in ("src", "scripts", "webapp", "db", "tests"):
        for path in (ROOT / folder).rglob("*.py"):
            if "node_modules" not in path.parts:
                yield path


def referenced_in_code() -> set[str]:
    names = {key for key, _ in PROVIDER_AUTODETECT}
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in CODE_PATTERNS:
            names |= set(pattern.findall(text))
        for group in LIST_PATTERN.findall(text):
            names |= set(re.findall(r"[A-Z][A-Z0-9_]+", group))
    return names


def referenced_in_deploy_config() -> set[str]:
    names = set()
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        names |= set(WORKFLOW_PATTERN.findall(path.read_text(encoding="utf-8")))
    names |= set(RENDER_PATTERN.findall((ROOT / "render.yaml").read_text(encoding="utf-8")))
    return names


def test_every_env_var_is_registered():
    unregistered = (referenced_in_code() | referenced_in_deploy_config()) - set(ENV_VARS) - {"GITHUB_TOKEN"}
    assert not unregistered, (
        f"Unregistered env vars {sorted(unregistered)}: add them to jobseeker.config.ENV_VARS "
        "-- after checking no existing variable already means the same thing."
    )


def test_no_dead_env_vars():
    dead = set(ENV_VARS) - referenced_in_code()
    assert not dead, f"Registered but never read: {sorted(dead)} -- remove them from ENV_VARS and deploy config."
