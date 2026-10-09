"""HTTP Basic Auth over HTTPS -- right-sized for a personal dashboard.

Authentication yields a Principal whose `account` scopes every user-owned
row (profile, resumes, drafts, feedback, applications, integrations).
The account comes from server configuration, never from the request, so
a client can't read or write another account's data by sending an id.
Changing the login username/password doesn't orphan data, because the
account id is separate from the credentials.
"""

from __future__ import annotations

import os
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


def require_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> Principal:
    expected_username = os.environ.get("DASHBOARD_USERNAME", "")
    expected_password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=500,
            detail="DASHBOARD_USERNAME/DASHBOARD_PASSWORD are not configured on the server.",
        )

    username_ok = secrets_module.compare_digest(credentials.username.encode(), expected_username.encode())
    password_ok = secrets_module.compare_digest(credentials.password.encode(), expected_password.encode())
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return Principal(username=credentials.username, account=os.environ.get("DASHBOARD_ACCOUNT_ID", "default"))


CurrentUser = Annotated[Principal, Depends(require_auth)]
