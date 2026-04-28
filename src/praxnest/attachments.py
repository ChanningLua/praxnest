"""File attachments — store on disk under data_dir/attachments/<sha256>.

Why content-addressed (sha256) storage:
- Same screenshot uploaded twice → one disk file, two metadata rows.
  Saves disk for big repeating PDFs / design assets.
- Filename in metadata is just for display; the actual file path is
  derived from sha256 so renaming is trivial (just update metadata).

Why not S3/object-storage by default:
- praxnest is local-first. The whole point is data doesn't leave the
  user's machine. S3 backend can ship as an optional driver in V0.3.

Image / PDF previews are handled by the browser via direct download
of the served bytes. We don't generate thumbnails — for V0.2.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from . import db


# Hard caps to keep V0.1/V0.2 focused on document attachments. Big-file
# / video uploads are out of scope for "team docs" use case.
MAX_BYTES = 25 * 1024 * 1024     # 25 MB per file
MAX_TOTAL_PER_WORKSPACE_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB per workspace

# MIME types we'll serve back without `Content-Disposition: attachment`.
# Keeps inline images from forcing a download. Anything not in this set
# gets attachment disposition for safety (don't open arbitrary HTML in-page).
INLINE_MIME_PREFIXES = ("image/", "application/pdf", "text/plain")


class AttachmentTooLarge(ValueError):
    pass


class AttachmentNotFound(LookupError):
    pass


class WorkspaceQuotaExceeded(ValueError):
    pass


@dataclass
class StoredAttachment:
    id: int
    workspace_id: int
    sha256: str
    filename: str
    mime_type: str
    size_bytes: int
    uploaded_by: int | None
    uploaded_at: str

    @classmethod
    def from_row(cls, row) -> "StoredAttachment":
        return cls(
            id=row["id"], workspace_id=row["workspace_id"], sha256=row["sha256"],
            filename=row["filename"], mime_type=row["mime_type"],
            size_bytes=row["size_bytes"], uploaded_by=row["uploaded_by"],
            uploaded_at=row["uploaded_at"],
        )


def _attachments_root(data_dir: Path) -> Path:
    p = Path(data_dir) / "attachments"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _disk_path(data_dir: Path, sha256: str) -> Path:
    """Two-level shard by leading 2 hex chars to keep any one folder
    from getting huge. ab/abc123... etc."""
    root = _attachments_root(data_dir)
    return root / sha256[:2] / sha256


def _workspace_total_bytes(conn, workspace_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM attachments WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchone()
    return int(row["total"])


def store(
    data_dir: Path,
    *,
    workspace_id: int,
    filename: str,
    mime_type: str,
    stream: BinaryIO,
    uploaded_by: int,
) -> StoredAttachment:
    """Read the stream into a temp file with sha256 as we go, then
    move into place + write metadata row. Atomic-ish: if anything
    fails before commit we don't leak orphan disk files."""
    if not filename or not filename.strip():
        raise ValueError("filename must not be empty")
    # Strip path components — we never trust upload-side filename for paths.
    filename = os.path.basename(filename.strip()) or "attachment"
    mime_type = (mime_type or "application/octet-stream").strip()

    # Hash + size while reading; reject early if oversize.
    hasher = hashlib.sha256()
    size = 0
    chunks: list[bytes] = []
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_BYTES:
            raise AttachmentTooLarge(f"file exceeds {MAX_BYTES // (1024 * 1024)} MB cap")
        hasher.update(chunk)
        chunks.append(chunk)
    sha256 = hasher.hexdigest()

    if size == 0:
        raise ValueError("file is empty")

    conn = db.connect(data_dir)
    try:
        # Workspace quota check.
        total = _workspace_total_bytes(conn, workspace_id)
        # Only count NEW bytes (we dedupe on sha256, but quota counts
        # the metadata row regardless — that's the user-visible "I've
        # uploaded N MB of stuff" number).
        if total + size > MAX_TOTAL_PER_WORKSPACE_BYTES:
            raise WorkspaceQuotaExceeded(
                f"workspace storage cap reached "
                f"({MAX_TOTAL_PER_WORKSPACE_BYTES // (1024 ** 3)} GB)"
            )

        target = _disk_path(data_dir, sha256)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                for chunk in chunks:
                    f.write(chunk)
            os.replace(tmp, target)

        cur = conn.execute(
            """
            INSERT INTO attachments
                (workspace_id, sha256, filename, mime_type, size_bytes, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, sha256, filename, mime_type, size, uploaded_by),
        )
        conn.commit()
        att_id = int(cur.lastrowid)
    finally:
        conn.close()

    return get(data_dir, attachment_id=att_id)


def get(data_dir: Path, *, attachment_id: int) -> StoredAttachment:
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            """
            SELECT id, workspace_id, sha256, filename, mime_type,
                   size_bytes, uploaded_by, uploaded_at
              FROM attachments WHERE id = ?
            """,
            (attachment_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AttachmentNotFound(f"attachment {attachment_id} not found")
    return StoredAttachment.from_row(row)


def list_for_workspace(data_dir: Path, workspace_id: int) -> list[StoredAttachment]:
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT id, workspace_id, sha256, filename, mime_type,
                   size_bytes, uploaded_by, uploaded_at
              FROM attachments WHERE workspace_id = ?
             ORDER BY id DESC
            """,
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()
    return [StoredAttachment.from_row(r) for r in rows]


def open_disk_file(data_dir: Path, sha256: str) -> Path:
    """Resolve the disk path for serving. Raises AttachmentNotFound if
    the metadata row exists but disk file is gone (corrupt state —
    surfaced rather than silently 404)."""
    p = _disk_path(data_dir, sha256)
    if not p.exists():
        raise AttachmentNotFound(f"attachment file missing on disk: {p}")
    return p


def is_inline_safe(mime_type: str) -> bool:
    """Is this content-type safe to serve inline (no attachment header)?

    Explicit deny for known XSS vectors (SVG can contain JavaScript;
    text/html obviously). The general rule: prefix-match the allow
    list, then deny on any of the dangerous types.
    """
    if not mime_type:
        return False
    mt = mime_type.lower()
    # Hard deny — these can XSS the page even if served same-origin.
    if mt in {"image/svg+xml", "image/svg", "text/html"} or mt.startswith("application/javascript"):
        return False
    return any(mt.startswith(prefix) for prefix in INLINE_MIME_PREFIXES)


def delete(data_dir: Path, *, attachment_id: int) -> bool:
    """Remove the metadata row. We DON'T garbage-collect the disk file
    here because another row may share the same sha256 (dedup). A
    separate housekeeping job (V0.3) sweeps orphan files."""
    conn = db.connect(data_dir)
    try:
        cur = conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
