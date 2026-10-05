from __future__ import annotations

from pydantic import BaseModel

VALID_STATUSES = {
    "new", "scored", "queued_for_digest", "sent_in_digest",
    "applied", "rejected", "excluded",
}


class JobUpdate(BaseModel):
    status: str | None = None
    outreach_draft: str | None = None
