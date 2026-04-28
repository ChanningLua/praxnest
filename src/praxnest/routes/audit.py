"""Audit log read endpoint.

For V0.1 we expose only a list endpoint; querying / filtering /
streaming all defer until we have a real UI surface to consume them.
Admins can already eyeball the SQLite db with any client.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import audit
from .auth import require_user


router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(require_user),
) -> dict[str, Any]:
    """Return the most recent audit events.

    Admin-only — events can include usernames + workspace names that
    a regular member shouldn't enumerate. Members can use git-blame-
    style attribution inside their own workspaces (Week 2+).
    """
    if user.get("role") != "admin":
        raise HTTPException(403, "audit log is admin-only")
    return {"events": audit.recent(request.app.state.data_dir, limit=limit)}
