"""Heartbeat + workspace online list."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import presence, workspaces
from .auth import require_user


router = APIRouter(prefix="/api", tags=["presence"])


@router.post("/heartbeat")
def heartbeat(request: Request, user=Depends(require_user)) -> dict:
    """Client pings this every ~30s while a tab is open. We just record
    "user X was alive at T" — no body, no fancy state machine. The
    client decides when to ping (typically: page load + every 30s while
    visible)."""
    presence.heartbeat(user["id"])
    return {"ok": True}


@router.get("/workspaces/{workspace_id}/online")
def online_in_workspace(
    workspace_id: int, request: Request, user=Depends(require_user),
) -> dict:
    """Members of this workspace whose heartbeat is fresh.

    Intersect (workspace members) ∩ (recently-active users). Both sets
    are small, so the join in Python is fine.
    """
    try:
        workspaces.assert_member(
            request.app.state.data_dir, workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")

    members = workspaces.list_members(request.app.state.data_dir, workspace_id)
    online_ids = set(presence.online_user_ids())
    online_members = [m for m in members if m["id"] in online_ids]
    return {
        "online": [
            {"id": m["id"], "username": m["username"], "workspace_role": m["workspace_role"]}
            for m in online_members
        ],
        "total_members": len(members),
    }
