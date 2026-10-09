"""The pipeline entry points actually run (they're what GitHub Actions calls)."""

import importlib.util
import json
import sys
from pathlib import Path

import sqlalchemy as sa

from conftest import make_listing
from jobseeker.database import jobs, runs

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_scripts_import():
    for path in SCRIPTS.glob("*.py"):
        load(path.stem)


def test_score_and_draft_end_to_end(engine, tmp_path, monkeypatch):
    payload = {"listings": [make_listing(url="https://jobs.lever.co/acme/e2e").to_dict(),
                            make_listing(source="linkedin", url="https://in.linkedin.com/jobs/backend-jobs").to_dict()],
               "source_stats": {"remoteok": {"found": 0, "error": "HTTP 403"}}, "raw_count": 5, "filtered_out": 3}
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps(payload))
    monkeypatch.setattr(sys, "argv", ["score_and_draft.py", "--jobs-file", str(jobs_file)])
    assert load("score_and_draft").main() == 0
    with engine.connect() as conn:
        run = conn.execute(sa.select(runs)).mappings().one()
        assert run["status"] == "partial" and run["jobs_new"] == 1 and run["jobs_rejected"] == 4 and run["jobs_found"] == 5
        assert conn.execute(sa.select(jobs.c.fit_score)).scalar() is not None


def test_v1_collect_output_still_accepted(tmp_path):
    jobs_file = tmp_path / "old.json"
    jobs_file.write_text(json.dumps([{"source": "remoteok", "url": "https://remoteok.com/x", "title": "T", "company": "C"}]))
    loaded = load("score_and_draft").load_collect_output(str(jobs_file))
    assert loaded["listings"][0].title == "T"


def test_digest_render_is_escaped_and_honest():
    digest = load("run_digest")
    html = digest.render_digest([{
        "id": 7, "title": "<script>x</script>", "url": "https://jobs.lever.co/a/1", "company": "Acme", "source": "lever",
        "fit_score": 82, "priority_score": 85, "fit_rationale": "Good", "posted_at_ts": None, "draft_body": "Hi",
    }], "https://jl.example.com")
    assert "<script>x" not in html and "posting date unavailable" in html and "https://jl.example.com/jobs/7" in html
    assert "Salary not stated" in html
