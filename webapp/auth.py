"""HTTP Basic Auth — the whole point of a hosted backend is that it's
reachable from anywhere, which also means anyone can find the URL. This is
a solo-use personal dashboard, not a multi-user product, so full OAuth
would be solving a problem that doesn't exist here; basic auth over HTTPS
is the right-sized protection for "keep strangers out of my job search."
"""

from __future__ import annotations

import os
import secrets as secrets_module
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


def require_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    expected_username = os.environ.get("DASHBOARD_USERNAME", "")
    expected_password = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=500,
            detail="DASHBOARD_USERNAME/DASHBOARD_PASSWORD are not configured on the server.",
        )

    username_ok = secrets_module.compare_digest(credentials.username, expected_username)
    password_ok = secrets_module.compare_digest(credentials.password, expected_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
