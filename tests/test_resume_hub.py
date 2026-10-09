import io
from pathlib import Path

import pytest
import sqlalchemy as sa

from jobseeker import profile, resume_hub
from jobseeker.database import resumes

FIXTURE = (Path(__file__).parent / "fixtures" / "drive_embedded_folder.html").read_text()
FOLDER = "https://drive.google.com/drive/folders/1RTE9C8BJOxQBT0GPVkiOyix4FjbTUI88?usp=sharing"


def docx(text: str) -> bytes:
    import docx as d

    doc = d.Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_folder_id_validation():
    assert resume_hub.folder_id(FOLDER) == "1RTE9C8BJOxQBT0GPVkiOyix4FjbTUI88"
    assert resume_hub.folder_id("1RTE9C8BJOxQBT0GPVkiOyix4FjbTUI88") == "1RTE9C8BJOxQBT0GPVkiOyix4FjbTUI88"
    for bad in ("https://evil.example.com/x", "http://169.254.169.254/latest", "", "../../etc"):
        with pytest.raises(resume_hub.HubError):
            resume_hub.folder_id(bad)


def test_listing_parser_keeps_documents_only():
    files = resume_hub.parse_folder_listing(FIXTURE)
    assert [(f.name, f.kind) for f in files] == [
        ("Deepak_Vaduganambi_Resume_Master_Draft.docx", "file"),
        ("Deepak_Vaduganambi_Resume_Master_Draft.pdf", "file"),
        ("resume16122023.docx", "file"),
        ("Cover notes", "gdoc"),
    ]  # the image and the subfolder are ignored


def test_sync_imports_versions_and_respects_deletions(engine):
    contents = {
        "1-0mtAWRxrf3lqChASzyTRE6wohWeZ0I9": docx("Master draft: backend engineer, Python, FastAPI, distributed systems at Oraczen."),
        "1TEcg4DE7S6b8EKNZEq5w_Tqtk7LaeGiJ": b"not a real pdf",  # fails validation -> reported, not fatal
        "1VFSuvqHtmsqLpGTQk5vboOdEf8rdb76B": docx("Older resume from 2023 with Django and REST APIs experience listed."),
        "1AbcDefGhiJklMnoPqrStu": docx("Cover notes: things I want to mention in applications, long enough text."),
    }
    lister = lambda fid: resume_hub.parse_folder_listing(FIXTURE)  # noqa: E731
    downloader = lambda f: (contents[f.id], f.name if f.kind == "file" else f"{f.name}.docx")  # noqa: E731
    with engine.begin() as conn:
        profile.get_or_seed(conn)  # seed resume is the initial default
        first = resume_hub.sync(conn, "default", FOLDER, lister=lister, downloader=downloader)
        assert sorted(first.imported) == ["Cover notes", "Deepak_Vaduganambi_Resume_Master_Draft.docx", "resume16122023.docx"]
        assert len(first.failed) == 1 and "Master_Draft.pdf" in first.failed[0]
        assert first.default_set_to == "Deepak_Vaduganambi_Resume_Master_Draft.docx"  # newest + "master"

        again = resume_hub.sync(conn, "default", FOLDER, lister=lister, downloader=downloader)
        assert again.imported == [] and len(again.unchanged) == 3 and again.default_set_to is None

        contents["1VFSuvqHtmsqLpGTQk5vboOdEf8rdb76B"] = docx("Updated older resume, now also mentions Kubernetes and Kafka.")
        changed = resume_hub.sync(conn, "default", FOLDER, lister=lister, downloader=downloader)
        assert changed.updated == ["resume16122023.docx"]
        versions = conn.execute(sa.select(resumes.c.version).where(
            resumes.c.drive_file_id == "1VFSuvqHtmsqLpGTQk5vboOdEf8rdb76B").order_by(resumes.c.version)).scalars().all()
        assert versions == [1, 2]  # old version kept

        conn.execute(sa.update(resumes).where(resumes.c.drive_file_id == "1AbcDefGhiJklMnoPqrStu").values(deleted_at=sa.func.now()))
        after_delete = resume_hub.sync(conn, "default", FOLDER, lister=lister, downloader=downloader)
        assert any("Cover notes" in s for s in after_delete.skipped)
        assert resume_hub.last_sync(conn, "default")["skipped"]


def test_hub_api(client, monkeypatch):
    assert client.post("/api/v2/resumes/hub/sync").status_code == 422  # no folder set yet
    current = client.get("/api/v2/profile").json()
    data = {**current["data"], "resume_folder_url": FOLDER}
    assert client.put("/api/v2/profile", json={"data": data, "version": current["version"]}).status_code == 200
    monkeypatch.setattr(resume_hub, "list_folder", lambda fid: [])
    body = client.post("/api/v2/resumes/hub/sync").json()
    assert body["imported"] == [] and body["failed"] == []
    assert client.get("/api/v2/resumes/hub").json()["folder_url"] == FOLDER
