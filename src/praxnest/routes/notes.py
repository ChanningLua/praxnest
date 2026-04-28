"""Notes routes — CRUD + search + backlinks.

All routes scope on workspace_id and verify membership before doing
anything; access control is centralized via ``workspaces.assert_member``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import audit, notes, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces/{workspace_id}/notes", tags=["notes"])


def _check_member(request: Request, workspace_id: int, user: dict) -> None:
    try:
        workspaces.assert_member(
            request.app.state.data_dir,
            workspace_id=workspace_id,
            user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")


class CreateNoteBody(BaseModel):
    folder_path: str = Field(default="", max_length=512)
    title: str = Field(..., min_length=1, max_length=200)
    body_md: str = Field(default="", max_length=2_000_000)


class UpdateNoteBody(BaseModel):
    expected_version: int = Field(..., ge=1)
    body_md: str | None = Field(default=None, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=200)


@router.get("")
def list_notes(workspace_id: int, request: Request, user=Depends(require_user)) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    return {"notes": notes.list_in_workspace(request.app.state.data_dir, workspace_id)}


@router.post("")
def create_note(
    workspace_id: int, body: CreateNoteBody, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir
    try:
        note = notes.create(
            data_dir,
            workspace_id=workspace_id,
            folder_path=body.folder_path,
            title=body.title,
            body_md=body.body_md,
            user_id=user["id"],
        )
    except notes.NoteAlreadyExists as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="note.create",
        target={"workspace_id": workspace_id, "note_id": note["id"], "title": note["title"]},
    )
    return note


@router.get("/{note_id}")
def get_note(workspace_id: int, note_id: int, request: Request, user=Depends(require_user)) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    try:
        note = notes.get(request.app.state.data_dir, note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "note not found")
    if note["workspace_id"] != workspace_id:
        # Don't 200 across workspaces — that'd be a quiet ACL leak.
        raise HTTPException(404, "note not found in this workspace")
    return note


@router.put("/{note_id}")
def update_note(
    workspace_id: int, note_id: int, body: UpdateNoteBody,
    request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    # Membership is checked on workspace; also verify this specific note
    # belongs to this workspace before we mutate.
    try:
        existing = notes.get(data_dir, note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "note not found")
    if existing["workspace_id"] != workspace_id:
        raise HTTPException(404, "note not found in this workspace")

    try:
        note = notes.update(
            data_dir,
            note_id=note_id,
            expected_version=body.expected_version,
            body_md=body.body_md,
            title=body.title,
            user_id=user["id"],
        )
    except notes.NoteVersionConflict as exc:
        # 409 + payload so the client can show the merge UI.
        raise HTTPException(
            409,
            detail={
                "error": "version_conflict",
                "current_version": exc.current_version,
                "current_body": exc.current_body,
            },
        ) from exc
    except notes.NoteAlreadyExists as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="note.update",
        target={"workspace_id": workspace_id, "note_id": note_id, "version": note["version"]},
    )
    return note


@router.delete("/{note_id}")
def delete_note(workspace_id: int, note_id: int, request: Request, user=Depends(require_user)) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir
    try:
        existing = notes.get(data_dir, note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "note not found")
    if existing["workspace_id"] != workspace_id:
        raise HTTPException(404, "note not found in this workspace")

    notes.delete(data_dir, note_id=note_id)
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="note.delete",
        target={"workspace_id": workspace_id, "note_id": note_id, "title": existing["title"]},
    )
    return {"deleted": True, "note_id": note_id}


@router.get("/{note_id}/backlinks")
def list_backlinks(
    workspace_id: int, note_id: int, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    """Notes in this workspace that wikilink to this one."""
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir
    try:
        note = notes.get(data_dir, note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "note not found")
    if note["workspace_id"] != workspace_id:
        raise HTTPException(404, "note not found in this workspace")

    return {
        "backlinks": notes.backlinks_to(
            data_dir, workspace_id=workspace_id, target_title=note["title"],
        ),
    }


# Search lives at workspace-level rather than under /notes because it spans
# all notes in a workspace (titles + bodies). Mounted as a separate router
# below; FastAPI lets us declare two routers with overlapping prefixes.

search_router = APIRouter(prefix="/api/workspaces/{workspace_id}", tags=["notes"])


@search_router.get("/search")
def search_notes(
    workspace_id: int,
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    return {
        "query": q,
        "results": notes.search(
            request.app.state.data_dir, workspace_id=workspace_id, query=q, limit=limit,
        ),
    }
