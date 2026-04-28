"""Comments + mentions routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import audit, comments as comments_lib, notes, notify as notify_lib, workspaces
from .auth import require_user


router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/notes/{note_id}/comments",
    tags=["comments"],
)
mentions_router = APIRouter(prefix="/api/me/mentions", tags=["mentions"])


class CreateCommentBody(BaseModel):
    body_md: str = Field(..., min_length=1, max_length=10_000)
    parent_id: int | None = Field(default=None, ge=1)


class UpdateCommentBody(BaseModel):
    body_md: str = Field(..., min_length=1, max_length=10_000)


class MarkReadBody(BaseModel):
    mention_ids: list[int] | None = Field(default=None)


def _check_member_and_note(request: Request, workspace_id: int, note_id: int, user: dict) -> dict:
    """Combined membership + note-belongs-to-workspace check. Returns
    the note row for downstream use."""
    data_dir = request.app.state.data_dir
    try:
        workspaces.assert_member(data_dir, workspace_id=workspace_id, user_id=user["id"])
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")

    try:
        note = notes.get(data_dir, note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "note not found")
    if note["workspace_id"] != workspace_id:
        raise HTTPException(404, "note not found in this workspace")
    return note


@router.get("")
def list_comments(
    workspace_id: int, note_id: int, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member_and_note(request, workspace_id, note_id, user)
    return {"comments": comments_lib.list_for_note(request.app.state.data_dir, note_id=note_id)}


@router.post("")
def create_comment(
    workspace_id: int, note_id: int, body: CreateCommentBody,
    request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member_and_note(request, workspace_id, note_id, user)
    data_dir = request.app.state.data_dir

    try:
        comment = comments_lib.create(
            data_dir, note_id=note_id, body_md=body.body_md,
            author_id=user["id"], author_username=user["username"],
            parent_id=body.parent_id, workspace_id=workspace_id,
        )
    except comments_lib.CommentNotFound as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="comment.create",
        target={
            "workspace_id": workspace_id, "note_id": note_id,
            "comment_id": comment["id"], "parent_id": body.parent_id,
        },
    )

    # Best-effort push notify for any @-mentions: we already inserted
    # mentions rows; now look up which channel each mentioned user has
    # picked (if any) and forward. Failure here doesn't fail the
    # comment save.
    _push_mention_notifications(
        data_dir, comment_id=comment["id"], actor_username=user["username"],
        workspace_id=workspace_id,
    )

    return comment


@router.put("/{comment_id}")
def update_comment(
    workspace_id: int, note_id: int, comment_id: int, body: UpdateCommentBody,
    request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member_and_note(request, workspace_id, note_id, user)
    data_dir = request.app.state.data_dir

    try:
        comment = comments_lib.update(
            data_dir, comment_id=comment_id, body_md=body.body_md,
            actor_id=user["id"], actor_role=user.get("role", "member"),
        )
    except comments_lib.CommentNotFound:
        raise HTTPException(404, "comment not found")
    except comments_lib.CommentForbidden as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if comment.get("note_id") != note_id:
        raise HTTPException(404, "comment not under this note")

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="comment.update",
        target={"workspace_id": workspace_id, "note_id": note_id, "comment_id": comment_id},
    )
    _push_mention_notifications(
        data_dir, comment_id=comment_id, actor_username=user["username"],
        workspace_id=workspace_id,
    )
    return comment


@router.delete("/{comment_id}")
def delete_comment(
    workspace_id: int, note_id: int, comment_id: int,
    request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member_and_note(request, workspace_id, note_id, user)
    data_dir = request.app.state.data_dir

    try:
        # Ensure the comment is under this note before letting anyone delete.
        comment = comments_lib.get(data_dir, comment_id=comment_id)
    except comments_lib.CommentNotFound:
        raise HTTPException(404, "comment not found")
    if comment["note_id"] != note_id:
        raise HTTPException(404, "comment not under this note")

    try:
        comments_lib.delete(
            data_dir, comment_id=comment_id, actor_id=user["id"],
            actor_role=user.get("role", "member"),
        )
    except comments_lib.CommentForbidden as exc:
        raise HTTPException(403, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="comment.delete",
        target={"workspace_id": workspace_id, "note_id": note_id, "comment_id": comment_id},
    )
    return {"deleted": True, "comment_id": comment_id}


# ── /api/me/mentions ────────────────────────────────────────────────────────


@mentions_router.get("")
def my_mentions(
    request: Request,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user=Depends(require_user),
) -> dict[str, Any]:
    data_dir = request.app.state.data_dir
    return {
        "mentions": comments_lib.list_mentions_for_user(
            data_dir, user_id=user["id"], unread_only=unread_only, limit=limit,
        ),
        "unread_count": comments_lib.unread_count(data_dir, user_id=user["id"]),
    }


@mentions_router.post("/mark-read")
def mark_my_mentions_read(
    body: MarkReadBody, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    n = comments_lib.mark_mentions_read(
        request.app.state.data_dir, user_id=user["id"],
        mention_ids=body.mention_ids,
    )
    return {"marked": n}


# ── helpers ────────────────────────────────────────────────────────────────


def _push_mention_notifications(
    data_dir, *, comment_id: int, actor_username: str, workspace_id: int,
) -> None:
    """For each unread mention created by this comment, look up the
    user's preferred notify channel (currently: just use the first
    notify.yaml channel; per-user preferences ship later) and push a
    short message. Silent on failure.

    This is best-effort — we never let a notify error fail the comment
    save, and we don't retry. Users have an in-app inbox anyway.
    """
    try:
        channels = notify_lib.list_channels()
    except Exception:
        return
    if not channels:
        return
    channel = channels[0]   # V0.4 simplification — first channel for everyone.

    # Look up the comment + every mention it triggered.
    conn = comments_lib.db.connect(data_dir)
    try:
        comment = conn.execute(
            "SELECT body_md, note_id FROM comments WHERE id = ?", (comment_id,),
        ).fetchone()
        if comment is None:
            return
        note_row = conn.execute(
            "SELECT title FROM notes WHERE id = ?", (comment["note_id"],),
        ).fetchone()
        if note_row is None:
            return
        # Mentions that haven't been notified yet — V0.4 doesn't track
        # notified-state separately, so we just push for every mention
        # row newly created. The route layer ensures we only call this
        # right after a create/update.
        rows = conn.execute(
            """
            SELECT m.id, u.username
              FROM mentions m
              JOIN users u ON u.id = m.mentioned_user_id
             WHERE m.source_kind = 'comment' AND m.source_id = ?
               AND m.read_at IS NULL
            """,
            (comment_id,),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        title = f"praxnest · {actor_username} 在《{note_row['title']}》提到了 @{row['username']}"
        body = (comment["body_md"] or "").strip()[:1000]
        try:
            notify_lib.push(channel=channel, title=title, body=body)
        except Exception:
            # Best-effort; the user still has the in-app inbox.
            continue
