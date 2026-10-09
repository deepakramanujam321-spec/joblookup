from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import sqlalchemy as sa

from jobseeker import google
from jobseeker.database import audit_log, oauth_integrations, oauth_states


class Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._p, self.text = status, payload or {}, text

    def json(self):
        return self._p


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("DASHBOARD_URL", "https://jl.example.com")


def _connect(engine, monkeypatch, service="gmail", scope=None):
    with engine.begin() as conn:
        url = google.start_authorization(conn, "default", service)
    params = parse_qs(urlparse(url).query)
    assert params["code_challenge_method"] == ["S256"] and params["redirect_uri"] == ["https://jl.example.com/api/v2/integrations/google/callback"]
    granted = scope or " ".join(google.SCOPES[service])
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": granted}))
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp(200, {"email": "me@example.com"}))
    with engine.begin() as conn:
        return google.complete_authorization(conn, "default", params["state"][0], "code")


def test_tokens_encrypted_and_state_single_use(engine, configured, monkeypatch):
    assert _connect(engine, monkeypatch) == "gmail"
    with engine.connect() as conn:
        row = conn.execute(sa.select(oauth_integrations)).one()
        assert row.account_email == "me@example.com" and "rt-1" not in row.refresh_token_enc
        assert google.decrypt(row.refresh_token_enc) == "rt-1"
        assert conn.execute(sa.select(sa.func.count()).select_from(oauth_states)).scalar() == 0
        assert conn.execute(sa.select(audit_log.c.action)).scalar() == "integration.connected"
    with engine.begin() as conn, pytest.raises(google.IntegrationError) as e:
        google.complete_authorization(conn, "default", "forged-state", "code")
    assert e.value.code == "invalid_state"


def test_state_from_another_account_rejected(engine, configured):
    with engine.begin() as conn:
        url = google.start_authorization(conn, "default", "drive")
    state = parse_qs(urlparse(url).query)["state"][0]
    with engine.begin() as conn, pytest.raises(google.IntegrationError):
        google.complete_authorization(conn, "intruder", state, "code")


def test_denied_scope_is_reported(engine, configured, monkeypatch):
    with pytest.raises(google.IntegrationError) as e:
        _connect(engine, monkeypatch, scope="openid email")
    assert e.value.code == "scope_denied"


def test_revoked_refresh_marks_integration(engine, configured, monkeypatch):
    _connect(engine, monkeypatch)
    with engine.begin() as conn:
        conn.execute(sa.update(oauth_integrations).values(access_token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp(400, text='{"error": "invalid_grant"}'))
    with engine.begin() as conn, pytest.raises(google.IntegrationError) as e:
        google.access_token(conn, "default", "gmail")
    assert e.value.code == "reconnect"
    with engine.connect() as conn:
        assert conn.execute(sa.select(oauth_integrations.c.status)).scalar() == "revoked"


def test_disconnect_one_service_keeps_other(engine, configured, monkeypatch):
    _connect(engine, monkeypatch, "gmail")
    _connect(engine, monkeypatch, "drive")
    revoked = []
    monkeypatch.setattr(requests, "post", lambda url, **k: revoked.append(url) or Resp(200))
    with engine.begin() as conn:
        google.disconnect(conn, "default", "drive")
    assert revoked == []  # revoking at Google would also kill Gmail
    with engine.begin() as conn:
        google.disconnect(conn, "default", "gmail")
    assert revoked == [google.REVOKE_URL]
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(oauth_integrations)).scalar() == 0


def test_unconfigured(engine):
    with engine.begin() as conn, pytest.raises(google.IntegrationError) as e:
        google.start_authorization(conn, "default", "gmail")
    assert e.value.code == "not_configured"
