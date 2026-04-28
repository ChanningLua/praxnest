"""Comments + @mentions on notes.

A comment is markdown text scoped to a note; one level of nesting via
``parent_id`` so users can reply but we don't end up rendering a
deeply-indented tree. Comments persist after the author leaves
(``author_username`` denormalized) so threads don't lose attribution.

@mentions are parsed from comment body at write time and stored in
the ``mentions`` table. Notification fan-out (push to user's wechat
etc.) happens in the route layer — this module just records who
should know.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import db


# A mention is `@username` where username matches our auth.username
# rules. We stop at whitespace / ASCII punctuation; CJK boundary
# isn't an issue because usernames are restricted to ASCII anyway.
_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_-]{0,63})")


class CommentNotFound(LookupError):
    pass


class CommentForbidden(PermissionError):
    pass


def extract_mentions(body_md: str) -> list[str]:
    """Distinct usernames mentioned via @ in the body, in order of
    first occurrence. Doesn't validate that the user actually exists —
    the caller resolves names → user_ids."""
    if not body_md:
        return []
    seen: dict[str, None] = {}
    for m in _MENTION_RE.finditer(body_md):
        name = m.group(1)
        if name and name not in seen:
            seen[name] = None
    return list(seen.keys())


def create(
    data_dir: Path,
    *,
    note_id: int,
    body_md: str,
    author_id: int,
    author_username: str,
    parent_id: int | None = None,
    workspace_id: int,
) -> dict[str, Any]:
    """Insert a comment + index any @mentions.

    Two-step but single transaction: the comment row + a mentions row
    per resolved @ get inserted together. If username resolution
    fails for any @, that mention is silently dropped — better than
    blowing up the whole save.

    `workspace_id` is required so we can stamp it on the mentions row
    for fast "unread @s in workspace X" queries; it must match the
    note's workspace (caller checks).
    """
    body_md = (body_md or "").strip()
    if not body_md:
        raise ValueError("comment body must not be empty")
    if len(body_md) > 10_000:
        raise ValueError("comment body max 10000 chars")
    if parent_id is not None and parent_id < 1:
        raise ValueError("parent_id must be a positive int or None")

    conn = db.connect(data_dir)
    try:
        # Validate parent comment belongs to same note (no cross-thread).
        if parent_id is not None:
            row = conn.execute(
                "SELECT note_id FROM comments WHERE id = ?", (parent_id,),
            ).fetchone()
            if row is None:
                raise CommentNotFound(f"parent comment {parent_id} not found")
            if row["note_id"] != note_id:
                raise ValueError("parent comment belongs to a different note")

        cur = conn.execute(
            """
            INSERT INTO comments (note_id, parent_id, body_md, author_id, author_username)
            VALUES (?, ?, ?, ?, ?)
            """,
            (note_id, parent_id, body_md, author_id, author_username),
        )
        comment_id = int(cur.lastrowid)

        # Resolve @mentions to actual user_ids and record them. Skip
        # the author themselves (mentioning yourself is fine but
        # generating a notification for yourself is noise).
        for username in extract_mentions(body_md):
            row = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,),
            ).fetchone()
            if row is None or row["id"] == author_id:
                continue
            conn.execute(
                """
                INSERT INTO mentions
                    (source_kind, source_id, mentioned_user_id, workspace_id)
                VALUES ('comment', ?, ?, ?)
                """,
                (comment_id, row["id"], workspace_id),
            )
        conn.commit()
    finally:
        conn.close()
    return get(data_dir, comment_id=comment_id)


def get(data_dir: Path, *, comment_id: int) -> dict[str, Any]:
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            """
            SELECT id, note_id, parent_id, body_md, author_id, author_username,
                   created_at, edited_at
              FROM comments WHERE id = ?
            """,
            (comment_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise CommentNotFound(f"comment {comment_id} not found")
    return dict(row)


def list_for_note(data_dir: Path, *, note_id: int) -> list[dict[str, Any]]:
    """All comments under a note, oldest first.

    Top-level + nested are returned flat with ``parent_id``; the GUI
    composes the tree. Keeps the query simple + lets GUI handle
    sort-within-thread freely.
    """
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT id, note_id, parent_id, body_md, author_id, author_username,
                   created_at, edited_at
              FROM comments WHERE note_id = ?
             ORDER BY created_at ASC
            """,
            (note_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def update(
    data_dir: Path,
    *,
    comment_id: int,
    body_md: str,
    actor_id: int,
    actor_role: str,
) -> dict[str, Any]:
    """Edit a comment's body. Author or system-admin only.

    Re-parses @mentions and merges into the mentions table — adds new
    @s, doesn't remove old ones (if you edit out a @ the original
    notification was already delivered; un-notifying would be weird).
    """
    body_md = (body_md or "").strip()
    if not body_md:
        raise ValueError("comment body must not be empty")
    if len(body_md) > 10_000:
        raise ValueError("comment body max 10000 chars")

    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            """
            SELECT c.author_id, c.note_id, n.workspace_id
              FROM comments c
              JOIN notes n ON n.id = c.note_id
             WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
        if row is None:
            raise CommentNotFound(f"comment {comment_id} not found")
        if row["author_id"] != actor_id and actor_role != "admin":
            raise CommentForbidden("only the author or a system admin can edit this comment")

        conn.execute(
            "UPDATE comments SET body_md = ?, edited_at = datetime('now') WHERE id = ?",
            (body_md, comment_id),
        )

        # Add NEW @mentions (since edit). Dedupe via existing index.
        for username in extract_mentions(body_md):
            urow = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,),
            ).fetchone()
            if urow is None or urow["id"] == actor_id:
                continue
            existing = conn.execute(
                """
                SELECT 1 FROM mentions
                 WHERE source_kind = 'comment' AND source_id = ?
                   AND mentioned_user_id = ?
                """,
                (comment_id, urow["id"]),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                """
                INSERT INTO mentions
                    (source_kind, source_id, mentioned_user_id, workspace_id)
                VALUES ('comment', ?, ?, ?)
                """,
                (comment_id, urow["id"], row["workspace_id"]),
            )
        conn.commit()
    finally:
        conn.close()
    return get(data_dir, comment_id=comment_id)


def delete(
    data_dir: Path, *, comment_id: int, actor_id: int, actor_role: str,
) -> bool:
    """Hard-delete. Author or system-admin only. Cascades to children
    and to mentions via FK."""
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT author_id FROM comments WHERE id = ?", (comment_id,),
        ).fetchone()
        if row is None:
            raise CommentNotFound(f"comment {comment_id} not found")
        if row["author_id"] != actor_id and actor_role != "admin":
            raise CommentForbidden("only the author or a system admin can delete this comment")

        cur = conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Mentions read state ────────────────────────────────────────────────────


def list_mentions_for_user(
    data_dir: Path, *, user_id: int, unread_only: bool = False, limit: int = 50,
) -> list[dict[str, Any]]:
    """Return @-mentions targeting `user_id`, newest first. Joins back
    to source comment + parent note so the UI can render "你被 X 在
    Note Y 里 @ 了" without extra round trips."""
    conn = db.connect(data_dir)
    try:
        sql = """
            SELECT m.id, m.source_kind, m.source_id, m.workspace_id, m.read_at, m.created_at,
                   c.body_md AS comment_body, c.author_username, c.note_id,
                   n.title AS note_title,
                   w.name AS workspace_name
              FROM mentions m
              LEFT JOIN comments c ON c.id = m.source_id AND m.source_kind = 'comment'
              LEFT JOIN notes n ON n.id = c.note_id
              LEFT JOIN workspaces w ON w.id = m.workspace_id
             WHERE m.mentioned_user_id = ?
        """
        params: list[Any] = [user_id]
        if unread_only:
            sql += " AND m.read_at IS NULL"
        sql += " ORDER BY m.id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def mark_mentions_read(
    data_dir: Path, *, user_id: int, mention_ids: list[int] | None = None,
) -> int:
    """Stamp read_at = now on the given mentions (or all unread for
    this user if no ids given). Returns affected rowcount."""
    conn = db.connect(data_dir)
    try:
        if mention_ids:
            # Defensive: only touch rows belonging to this user.
            placeholders = ",".join("?" for _ in mention_ids)
            cur = conn.execute(
                f"""
                UPDATE mentions
                   SET read_at = datetime('now')
                 WHERE mentioned_user_id = ?
                   AND read_at IS NULL
                   AND id IN ({placeholders})
                """,
                [user_id, *mention_ids],
            )
        else:
            cur = conn.execute(
                """
                UPDATE mentions
                   SET read_at = datetime('now')
                 WHERE mentioned_user_id = ? AND read_at IS NULL
                """,
                (user_id,),
            )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def unread_count(data_dir: Path, *, user_id: int) -> int:
    """Cheap count — used by the topbar badge."""
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM mentions WHERE mentioned_user_id = ? AND read_at IS NULL",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"])
