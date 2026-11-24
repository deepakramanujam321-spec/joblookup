"""HTTP Basic Auth over HTTPS -- right-sized for a small, fixed set of people.

Authentication yields a Principal whose `account` scopes every user-owned
row (profile, resumes, jobs, drafts, feedback, applications, integrations).
The account comes from server configuration, never from the request, so
a client can't read or write another account's data by sending an id.
Changing a login username/password doesn't orphan data, because the
account id is separate from the credentials.

Logins: DASHBOARD_USERNAME / DASHBOARD_PASSWORD (account DASHBOARD_ACCOUNT_ID,
default 'default') is the original one. More people are added with
DASHBOARD_EXTRA_USERS, a JSON list:
    [{"username": "priya", "password": "...", "account": "priya"}]
"""

from __future__ import annotations

import json
import os
import re
import secrets as secrets_module
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


@dataclass(frozen=True)
class Principal:
    username: str
    account: str


_ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def configured_logins() -> list[tuple[str, str, str]]:
    """(username, password, account) for every configured person."""
    logins = []
    username, password = os.environ.get("DASHBOARD_USERNAME", ""), os.environ.get("DASHBOARD_PASSWORD", "")
    if username and password:
        logins.append((username, password, os.environ.get("DASHBOARD_ACCOUNT_ID", "default")))
    raw = os.environ.get("DASHBOARD_EXTRA_USERS", "").strip()
    if raw:
        try:
            extra = json.loads(raw)
            for entry in extra:
                login = (str(entry["username"]), str(entry["password"]), str(entry["account"]))
                if not all(login) or not _ACCOUNT_RE.match(login[2]):
                    raise ValueError("username, password and a lowercase account id are required")
                logins.append(login)
        except (ValueError, KeyError, TypeError) as e:  # misconfiguration: say so, never silently ignore a login
            raise HTTPException(status_code=500, detail=f"DASHBOARD_EXTRA_USERS is invalid: {e}") from e
    return logins


def require_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> Principal:
    logins = configured_logins()
    if not logins:
        raise HTTPException(
            status_code=500,
            detail="DASHBOARD_USERNAME/DASHBOARD_PASSWORD are not configured on the server.",
        )

    given_user, given_password = credentials.username.encode(), credentials.password.encode()
    matched = None
    for username, password, account in logins:  # check all of them: timing doesn't reveal which login exists
        username_ok = secrets_module.compare_digest(given_user, username.encode())
        password_ok = secrets_module.compare_digest(given_password, password.encode())
        if username_ok and password_ok:
            matched = account
    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return Principal(username=credentials.username, account=matched)


CurrentUser = Annotated[Principal, Depends(require_auth)]
