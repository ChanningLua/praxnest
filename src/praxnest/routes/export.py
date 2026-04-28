"""Workspace export — `GET /api/workspaces/{ws}/export`.

Returns a zip stream. Workspace admin only — admins routinely have
access to all data in their space; we don't want random members
yanking the whole archive.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from .. import audit, exporter, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces/{workspace_id}/export", tags=["export"])


@router.get("")
def export_workspace(
    workspace_id: int, request: Request, user=Depends(require_user),
) -> Response:
    data_dir = request.app.state.data_dir

    # Workspace-admin gate. System admin alone isn't enough — must be
    # a member-admin of THIS workspace (mirrors invite/remove rules).
    try:
        ws_role = workspaces.assert_member(
            data_dir, workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")
    if ws_role != "admin":
        raise HTTPException(403, "workspace admin required")

    try:
        ws = workspaces.get(data_dir, workspace_id)
    except workspaces.WorkspaceNotFound:
        raise HTTPException(404, "workspace not found")

    blob = exporter.export_workspace(data_dir, workspace_id=workspace_id)

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="workspace.export",
        target={"workspace_id": workspace_id, "size_bytes": len(blob)},
    )

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", ws["name"]) or "workspace"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="praxnest-{safe_name}.zip"',
        },
    )
