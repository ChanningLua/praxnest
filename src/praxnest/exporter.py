"""Export a workspace to a zip — markdown + structured json sidecars.

Layout in the resulting zip:

    notes/<folder>/<title>.md           — note bodies, mirrors workspace tree
    comments.json                        — all comments + author + thread structure
    tasks.json                           — task list with assignees / status / due
    members.json                         — workspace member list (no passwords)
    attachments/<sha>.<ext>              — original file bytes (deduped by sha)
    attachments-index.json               — id → filename → sha → mime
    metadata.json                        — workspace + export timestamp + counts

This is bulk export for migration / backup / GDPR. Designed to be
re-importable in V0.6+. We keep the markdown human-readable so users
can grep / edit even without praxnest.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import attachments as attachments_lib
from . import comments as comments_lib
from . import db
from . import notes
from . import tasks as tasks_lib
from . import workspaces


_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(s: str) -> str:
    """Sanitize a string for use as a filename inside the zip.

    Replaces characters illegal on Windows / weird-on-Unix with `_`.
    Caps length so the resulting path stays under typical fs limits.
    """
    s = s.strip().replace("\x00", "")
    s = _FORBIDDEN_FILENAME_CHARS.sub("_", s)
    s = s.strip(". ")  # trailing dot/space is invalid on Windows
    return (s or "untitled")[:120]


def _serialize_metadata(workspace_id: int, ws_name: str, counts: dict[str, int]) -> str:
    return json.dumps({
        "workspace_id": workspace_id,
        "workspace_name": ws_name,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "praxnest_version": _read_version(),
        "counts": counts,
    }, indent=2, ensure_ascii=False)


def _read_version() -> str:
    from . import __version__
    return __version__


def export_workspace(data_dir: Path, *, workspace_id: int) -> bytes:
    """Build the zip in memory and return its bytes.

    For very large workspaces this is memory-bound; v0.6+ will switch
    to streaming. For the scoped 5-30 person team use case (1k notes,
    a few hundred MB of attachments), in-memory is fine.
    """
    ws = workspaces.get(data_dir, workspace_id)
    note_rows = notes.list_in_workspace(data_dir, workspace_id)
    member_rows = workspaces.list_members(data_dir, workspace_id)
    task_rows = tasks_lib.list_for_workspace(data_dir, workspace_id=workspace_id, limit=500)
    attachment_rows = attachments_lib.list_for_workspace(data_dir, workspace_id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:

        # ── notes/ — markdown files keyed by folder/title ────────────────
        for n in note_rows:
            full = notes.get(data_dir, n["id"])
            folder = full.get("folder_path") or ""
            folder_safe = "/".join(_safe_filename(p) for p in folder.split("/")) if folder else ""
            file_name = _safe_filename(full["title"]) + ".md"
            arc_path = f"notes/{folder_safe}/{file_name}" if folder_safe else f"notes/{file_name}"
            # Frontmatter so re-import can recover metadata.
            frontmatter = (
                "---\n"
                f"praxnest_note_id: {full['id']}\n"
                f"title: {json.dumps(full['title'], ensure_ascii=False)}\n"
                f"folder_path: {json.dumps(full.get('folder_path') or '', ensure_ascii=False)}\n"
                f"version: {full.get('version', 1)}\n"
                f"created_at: {full.get('created_at', '')}\n"
                f"updated_at: {full.get('updated_at', '')}\n"
                "---\n\n"
            )
            zf.writestr(arc_path, frontmatter + (full.get("body_md") or ""))

        # ── comments.json — full thread structure preserved ──────────────
        all_comments: list[dict[str, Any]] = []
        for n in note_rows:
            note_comments = comments_lib.list_for_note(data_dir, note_id=n["id"])
            for c in note_comments:
                # Fetch the body field (list_for_note already includes it).
                all_comments.append(c)
        zf.writestr("comments.json", json.dumps(all_comments, indent=2, ensure_ascii=False, default=str))

        # ── tasks.json ──────────────────────────────────────────────────
        full_tasks = [tasks_lib.get(data_dir, task_id=t["id"]) for t in task_rows]
        zf.writestr("tasks.json", json.dumps(full_tasks, indent=2, ensure_ascii=False, default=str))

        # ── members.json — never include password hashes ────────────────
        members_safe = [
            {
                "user_id": m["id"],
                "username": m["username"],
                "workspace_role": m["workspace_role"],
                "system_role": m.get("user_role"),
                "added_at": m["added_at"],
            }
            for m in member_rows
        ]
        zf.writestr("members.json", json.dumps(members_safe, indent=2, ensure_ascii=False))

        # ── attachments/ + index ────────────────────────────────────────
        # Dedupe at write time: the same sha may appear in multiple
        # attachment rows (different filenames pointing at same bytes).
        # Write each disk file once, but keep the metadata for all rows.
        written_shas: set[str] = set()
        attachment_index: list[dict[str, Any]] = []
        for a in attachment_rows:
            attachment_index.append({
                "id": a.id,
                "filename": a.filename,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
                "sha256": a.sha256,
                "uploaded_at": a.uploaded_at,
                "uploaded_by": a.uploaded_by,
                # Path inside zip — same sha → same path so the index
                # entries can dedupe-share a file.
                "zip_path": f"attachments/{a.sha256[:2]}/{a.sha256}",
            })
            if a.sha256 in written_shas:
                continue
            try:
                disk_path = attachments_lib.open_disk_file(data_dir, a.sha256)
            except attachments_lib.AttachmentNotFound:
                # Metadata orphan (rare). Skip the file but keep the index entry
                # so the user can see it existed.
                continue
            zf.writestr(f"attachments/{a.sha256[:2]}/{a.sha256}", disk_path.read_bytes())
            written_shas.add(a.sha256)

        zf.writestr(
            "attachments-index.json",
            json.dumps(attachment_index, indent=2, ensure_ascii=False),
        )

        # ── metadata.json — overall summary at zip root ────────────────
        zf.writestr(
            "metadata.json",
            _serialize_metadata(
                workspace_id, ws["name"],
                counts={
                    "notes": len(note_rows),
                    "comments": len(all_comments),
                    "tasks": len(full_tasks),
                    "members": len(member_rows),
                    "attachments": len(attachment_rows),
                    "attachment_disk_files": len(written_shas),
                },
            ),
        )

    return buf.getvalue()
