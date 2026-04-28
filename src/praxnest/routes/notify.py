"""Notify routes — push the current note (or arbitrary text) to a
configured prax notify channel."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, notes, notify as notify_lib, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces/{workspace_id}/notify", tags=["notify"])


class PushBody(BaseModel):
    channel: str = Field(..., min_length=1, max_length=64)
    # Either push a note's body...
    note_id: int | None = Field(default=None, ge=1)
    # ...or push arbitrary content (e.g. an AI workflow output the
    # user wants to forward without saving as a note first).
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=20_000)


def _check_member(request: Request, workspace_id: int, user: dict) -> None:
    try:
        workspaces.assert_member(
            request.app.state.data_dir,
            workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")


@router.get("/channels")
def list_channels_route(workspace_id: int, request: Request, user=Depends(require_user)) -> dict:
    """Return the channel names configured in ``~/.prax/notify.yaml``.

    Notifies the GUI which channels exist so it can populate a dropdown
    — no need to re-prompt the user for channel config in praxnest.
    """
    _check_member(request, workspace_id, user)
    return {"channels": notify_lib.list_channels()}


@router.post("/push")
def push_route(
    workspace_id: int, body: PushBody, request: Request, user=Depends(require_user),
) -> dict:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    title = (body.title or "").strip()
    text = body.body or ""

    if body.note_id is not None:
        try:
            note = notes.get(data_dir, body.note_id)
        except notes.NoteNotFound:
            raise HTTPException(404, "note not found")
        if note["workspace_id"] != workspace_id:
            raise HTTPException(404, "note not in this workspace")
        if not title:
            title = note["title"]
        if not text:
            text = note["body_md"]

    if not text.strip():
        raise HTTPException(400, "nothing to push (no body or note_id)")

    try:
        result = notify_lib.push(channel=body.channel, title=title or "praxnest 推送", body=text)
    except notify_lib.PraxNotInstalled as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="notify.push",
        target={
            "workspace_id": workspace_id, "channel": body.channel,
            "ok": result.ok, "error": result.error[:200],
        },
    )
    if not result.ok:
        # 502: upstream prax/iLink failed — distinct from our 4xx (validation).
        raise HTTPException(502, f"push failed: {result.error}")
    return {"ok": True, "channel": result.channel}
