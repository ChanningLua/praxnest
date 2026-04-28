"""Notes CRUD + double-link parsing + FTS5 search.

A note is one markdown document. The (workspace_id, folder_path,
title) triple is unique — clients can browse by folder and title is
the displayed name. ``folder_path`` is a forward-slash-joined string
('foo/bar') with no leading slash; empty string = workspace root.

Double links use the Obsidian convention: ``[[Other Note Title]]``
matches by title within the same workspace. Reverse-references are
computed at query time (no separate index table for v0.1 — link
density is low enough that a LIKE query is fast).

Versioning is integer-incremented per save, used for last-write-wins
conflict detection: clients send the version they loaded; if it
doesn't match the server's current version, save is rejected with a
409 and the client decides whether to overwrite.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import db


WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]{1,128})\]\]")


class NoteNotFound(LookupError):
    pass


class NoteVersionConflict(Exception):
    """Raised when client's note.version != server's current version."""

    def __init__(self, *, current_version: int, current_body: str):
        super().__init__(f"version conflict (server at {current_version})")
        self.current_version = current_version
        self.current_body = current_body


class NoteAlreadyExists(ValueError):
    pass


def _normalize_folder(folder_path: str) -> str:
    """Strip leading/trailing slashes; collapse repeated separators.
    Empty string = workspace root."""
    if folder_path is None:
        return ""
    parts = [p for p in folder_path.replace("\\", "/").strip("/").split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("folder_path must not contain '..'")
    return "/".join(parts)


def create(
    data_dir: Path,
    *,
    workspace_id: int,
    folder_path: str = "",
    title: str,
    body_md: str = "",
    user_id: int,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > 200:
        raise ValueError("title max 200 chars")
    folder_path = _normalize_folder(folder_path)

    conn = db.connect(data_dir)
    try:
        try:
            cur = conn.execute(
                """
                INSERT INTO notes (workspace_id, folder_path, title, body_md, version, updated_by)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (workspace_id, folder_path, title, body_md, user_id),
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "unique" in msg or "constraint" in msg:
                raise NoteAlreadyExists(
                    f"note {title!r} already exists in workspace {workspace_id} folder {folder_path!r}"
                ) from None
            raise
        note_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return get(data_dir, note_id)


def get(data_dir: Path, note_id: int) -> dict[str, Any]:
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            """
            SELECT id, workspace_id, folder_path, title, body_md, version,
                   created_at, updated_at, updated_by
              FROM notes WHERE id = ?
            """,
            (note_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise NoteNotFound(f"note {note_id} not found")
    return dict(row)


def list_in_workspace(data_dir: Path, workspace_id: int) -> list[dict[str, Any]]:
    """All notes in a workspace, ordered by folder then title.

    The GUI builds the file tree client-side from this flat list —
    cheap on the server, simple to test, and lets us refresh by
    diffing in-place rather than re-fetching subtrees.
    """
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT id, folder_path, title, version, updated_at
              FROM notes WHERE workspace_id = ?
             ORDER BY folder_path ASC, title ASC
            """,
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def update(
    data_dir: Path,
    *,
    note_id: int,
    expected_version: int,
    body_md: str | None = None,
    title: str | None = None,
    user_id: int,
) -> dict[str, Any]:
    """Last-write-wins update.

    The client sends the version it loaded. If the db has moved past
    that version (someone else saved in between), we raise
    ``NoteVersionConflict`` containing the current state. The client
    decides whether to overwrite (call ``update`` again with the new
    version) or surface a merge UI to the user.
    """
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT version, body_md, title FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row is None:
            raise NoteNotFound(f"note {note_id} not found")
        if row["version"] != expected_version:
            raise NoteVersionConflict(
                current_version=row["version"],
                current_body=row["body_md"],
            )

        new_body = row["body_md"] if body_md is None else body_md
        new_title = row["title"] if title is None else (title or "").strip()
        if not new_title:
            raise ValueError("title must not be empty")
        new_version = row["version"] + 1

        try:
            conn.execute(
                """
                UPDATE notes
                   SET body_md = ?, title = ?, version = ?,
                       updated_at = datetime('now'), updated_by = ?
                 WHERE id = ? AND version = ?
                """,
                (new_body, new_title, new_version, user_id, note_id, expected_version),
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "unique" in msg or "constraint" in msg:
                raise NoteAlreadyExists(f"another note named {new_title!r} exists in this folder") from None
            raise
        conn.commit()
    finally:
        conn.close()
    return get(data_dir, note_id)


def delete(data_dir: Path, *, note_id: int) -> bool:
    """Hard-delete. Returns True if a row was removed."""
    conn = db.connect(data_dir)
    try:
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Wikilinks + reverse-references ──────────────────────────────────────────


def extract_links(body_md: str) -> list[str]:
    """Return distinct ``[[wiki link]]`` titles found in the body.

    Order is first-occurrence; duplicates collapsed. We don't attempt
    to resolve them here — see ``backlinks_to`` for that.
    """
    if not body_md:
        return []
    seen: dict[str, None] = {}
    for m in WIKILINK_RE.finditer(body_md):
        target = m.group(1).strip()
        if target and target not in seen:
            seen[target] = None
    return list(seen.keys())


def backlinks_to(
    data_dir: Path, *, workspace_id: int, target_title: str
) -> list[dict[str, Any]]:
    """Notes in this workspace that contain ``[[<target_title>]]``.

    SQL LIKE on the body is fine for v0.1; if links per workspace
    explode we'll move to a dedicated link index table.
    """
    if not target_title:
        return []
    pattern = f"%[[{target_title}]]%"
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT id, folder_path, title, updated_at
              FROM notes
             WHERE workspace_id = ? AND body_md LIKE ?
               AND title != ?
             ORDER BY title ASC
            """,
            (workspace_id, pattern, target_title),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── Full-text search ────────────────────────────────────────────────────────


def search(
    data_dir: Path, *, workspace_id: int, query: str, limit: int = 50
) -> list[dict[str, Any]]:
    """FTS5 search across title + body. Title hits rank above body hits
    via the bm25 weighting.

    Returns title, folder_path, snippet, rank — enough for the GUI to
    render a results panel.
    """
    if not query or not query.strip():
        return []
    limit = max(1, min(int(limit), 200))

    # Escape FTS5 syntax-significant chars by quoting the whole query.
    # We don't expose the FTS5 mini-language to users (yet) — the
    # tradeoff being: no `OR` / `NEAR` for now, but no syntax-error
    # surprises either.
    safe_query = '"' + query.replace('"', '""') + '"'

    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT n.id, n.folder_path, n.title,
                   snippet(notes_fts, 1, '<mark>', '</mark>', '…', 12) AS snippet,
                   bm25(notes_fts, 2.0, 1.0) AS rank
              FROM notes_fts
              JOIN notes n ON n.id = notes_fts.rowid
             WHERE notes_fts MATCH ? AND n.workspace_id = ?
             ORDER BY rank
             LIMIT ?
            """,
            (safe_query, workspace_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
