"""Attachment upload + serve.

Two routers because the URL shapes differ:
- POST /api/workspaces/{ws}/attachments — multipart upload (workspace-scoped)
- GET  /api/attachments/{id}            — serve back to browser

The serve route uses ``id`` (not sha256) so deletes work intuitively:
delete the metadata row, the URL 404s, even though the disk file may
still exist (because another row uses the same sha256).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .. import attachments as att, audit, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces/{workspace_id}/attachments", tags=["attachments"])
serve_router = APIRouter(prefix="/api/attachments", tags=["attachments"])


def _check_member(request: Request, workspace_id: int, user: dict) -> None:
    try:
        workspaces.assert_member(
            request.app.state.data_dir,
            workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")


@router.post("")
async def upload_attachment(
    workspace_id: int,
    request: Request,
    file: UploadFile = File(...),
    user=Depends(require_user),
) -> JSONResponse:
    """Upload one file. Returns the attachment metadata + a markdown
    snippet the client can paste into a note (`![](attachment://N)` for
    images, `[filename](attachment://N)` otherwise)."""
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    # FastAPI's UploadFile has a SpooledTemporaryFile we can read in chunks
    # via the underlying ``.file``. ``store`` consumes it lazily so we don't
    # buffer the whole upload in memory.
    try:
        record = att.store(
            data_dir,
            workspace_id=workspace_id,
            filename=file.filename or "attachment",
            mime_type=file.content_type or "application/octet-stream",
            stream=file.file,
            uploaded_by=user["id"],
        )
    except att.AttachmentTooLarge as exc:
        raise HTTPException(413, str(exc))
    except att.WorkspaceQuotaExceeded as exc:
        raise HTTPException(413, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="attachment.upload",
        target={
            "workspace_id": workspace_id,
            "attachment_id": record.id,
            "filename": record.filename,
            "size_bytes": record.size_bytes,
        },
    )

    # Build the markdown the user can paste. Image vs link form so
    # screenshots render inline and other files show as clickable links.
    is_image = (record.mime_type or "").lower().startswith("image/")
    md_snippet = (
        f"![{record.filename}](/api/attachments/{record.id})"
        if is_image
        else f"[{record.filename}](/api/attachments/{record.id})"
    )

    return JSONResponse({
        "id": record.id,
        "filename": record.filename,
        "mime_type": record.mime_type,
        "size_bytes": record.size_bytes,
        "uploaded_at": record.uploaded_at,
        "url": f"/api/attachments/{record.id}",
        "md_snippet": md_snippet,
    })


@router.get("")
def list_attachments(
    workspace_id: int, request: Request, user=Depends(require_user),
) -> dict:
    _check_member(request, workspace_id, user)
    rows = att.list_for_workspace(request.app.state.data_dir, workspace_id)
    return {
        "attachments": [
            {
                "id": r.id, "filename": r.filename, "mime_type": r.mime_type,
                "size_bytes": r.size_bytes, "uploaded_at": r.uploaded_at,
                "url": f"/api/attachments/{r.id}",
            }
            for r in rows
        ]
    }


@router.delete("/{attachment_id}")
def delete_attachment(
    workspace_id: int, attachment_id: int, request: Request, user=Depends(require_user),
) -> dict:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir
    try:
        record = att.get(data_dir, attachment_id=attachment_id)
    except att.AttachmentNotFound:
        raise HTTPException(404, "attachment not found")
    if record.workspace_id != workspace_id:
        raise HTTPException(404, "attachment not in this workspace")

    att.delete(data_dir, attachment_id=attachment_id)
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="attachment.delete",
        target={"workspace_id": workspace_id, "attachment_id": attachment_id, "filename": record.filename},
    )
    return {"deleted": True, "attachment_id": attachment_id}


@serve_router.get("/{attachment_id}")
def serve_attachment(
    attachment_id: int, request: Request, user=Depends(require_user),
) -> FileResponse:
    """Stream the file back. ACL: caller must be a member of the
    attachment's workspace. Without this, anyone with a logged-in
    session could enumerate attachment ids by guessing.
    """
    data_dir = request.app.state.data_dir
    try:
        record = att.get(data_dir, attachment_id=attachment_id)
    except att.AttachmentNotFound:
        raise HTTPException(404, "attachment not found")

    try:
        workspaces.assert_member(
            data_dir, workspace_id=record.workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        # 404 (not 403) — same idempotent enumeration-defense rule we use for notes.
        raise HTTPException(404, "attachment not found")

    try:
        path = att.open_disk_file(data_dir, record.sha256)
    except att.AttachmentNotFound:
        raise HTTPException(410, "attachment metadata exists but file missing on disk")

    # Decide inline vs attachment disposition. Images / pdfs / plaintext
    # show inline; everything else forces download to avoid serving HTML
    # / SVG that could XSS the same-origin page.
    disposition = "inline" if att.is_inline_safe(record.mime_type) else "attachment"
    return FileResponse(
        path,
        media_type=record.mime_type,
        filename=record.filename,
        headers={"Content-Disposition": f'{disposition}; filename="{record.filename}"'},
    )
