"""Team memory routes — find similar notes across this workspace or
across all workspaces the caller belongs to.

This is the GUI's "📦 团队记忆" panel: while you're writing, hit this
endpoint with your current draft body and get back the top-N notes
whose content overlaps. V0.1 backend = FTS5 keyword similarity (see
`memory.py`); V0.2 swaps to vector embeddings without changing this
route's contract.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import memory, notes, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces/{workspace_id}/memory", tags=["memory"])
cross_router = APIRouter(prefix="/api/memory", tags=["memory"])


def _check_member(request: Request, workspace_id: int, user: dict) -> None:
    try:
        workspaces.assert_member(
            request.app.state.data_dir,
            workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")


@router.get("/similar")
def similar_in_workspace(
    workspace_id: int,
    request: Request,
    note_id: int = Query(..., ge=1),
    top_k: int = Query(default=5, ge=1, le=20),
    user=Depends(require_user),
) -> dict[str, Any]:
    """Notes in this workspace whose content overlaps with note_id's body."""
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    try:
        anchor = notes.get(data_dir, note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "anchor note not found")
    if anchor["workspace_id"] != workspace_id:
        raise HTTPException(404, "anchor note not in this workspace")

    results = memory.find_similar(
        data_dir,
        workspace_id=workspace_id,
        body_md=anchor["body_md"],
        exclude_note_id=note_id,
        top_k=top_k,
    )
    return {"anchor_note_id": note_id, "results": results}


@cross_router.get("/similar-across-workspaces")
def similar_across_workspaces(
    request: Request,
    note_id: int = Query(..., ge=1),
    top_k: int = Query(default=5, ge=1, le=20),
    user=Depends(require_user),
) -> dict[str, Any]:
    """Cross-workspace recall — finds related notes in ANY workspace
    the caller is a member of. Excludes the anchor note itself."""
    data_dir = request.app.state.data_dir
    try:
        anchor = notes.get(data_dir, note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "anchor note not found")

    # Membership check on the anchor's workspace (caller must at least
    # have access to where the anchor lives — otherwise they're using
    # this endpoint to peek at unknown notes).
    try:
        workspaces.assert_member(
            data_dir, workspace_id=anchor["workspace_id"], user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "anchor note not found or not accessible")

    results = memory.find_similar_across_workspaces(
        data_dir, user_id=user["id"], body_md=anchor["body_md"], top_k=top_k,
    )
    # Always exclude the anchor itself from results.
    results = [r for r in results if r["id"] != note_id]
    return {"anchor_note_id": note_id, "results": results}
