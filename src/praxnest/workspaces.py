"""Workspace CRUD + membership helpers.

A workspace is the top-level container that groups a folder tree of
notes. Every user belongs to one or more workspaces (via
``workspace_members``); cross-workspace operations (e.g. team-memory
search across all workspaces a user can see) layer on top of these.

For v0.1 we keep the model deliberately thin: name + admin-creator +
member list. Notion-style nested workspaces, public workspaces,
guest links — all deferred.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db


class WorkspaceAlreadyExists(ValueError):
    pass


class WorkspaceNotFound(LookupError):
    pass


class NotAMember(PermissionError):
    pass


def create(data_dir: Path, *, name: str, created_by: int) -> int:
    """Insert a workspace row + auto-add creator as admin member.

    Returns workspace_id. Two-step transaction so we never end up with
    an orphan workspace nobody can see.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("workspace name must not be empty")
    if len(name) > 64:
        raise ValueError("workspace name max 64 chars")

    conn = db.connect(data_dir)
    try:
        try:
            cur = conn.execute(
                "INSERT INTO workspaces (name, created_by) VALUES (?, ?)",
                (name, created_by),
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "unique" in msg or "constraint" in msg:
                raise WorkspaceAlreadyExists(f"workspace {name!r} already exists") from None
            raise
        ws_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (?, ?, 'admin')",
            (ws_id, created_by),
        )
        conn.commit()
        return ws_id
    finally:
        conn.close()


def list_for_user(data_dir: Path, user_id: int) -> list[dict[str, Any]]:
    """All workspaces this user can see, newest first."""
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT w.id, w.name, w.created_at, m.role AS member_role
              FROM workspaces w
              JOIN workspace_members m ON m.workspace_id = w.id
             WHERE m.user_id = ?
             ORDER BY w.id DESC
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get(data_dir: Path, workspace_id: int) -> dict[str, Any]:
    """Fetch one workspace by id; raises WorkspaceNotFound."""
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT id, name, created_at, created_by FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise WorkspaceNotFound(f"workspace {workspace_id} not found")
    return dict(row)


def assert_member(data_dir: Path, *, workspace_id: int, user_id: int) -> str:
    """Raises NotAMember if the user can't see this workspace.
    Returns the user's role in that workspace ('admin' or 'member').

    Every notes / workflow / memory operation calls this — single
    point of access control.
    """
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise NotAMember(f"user {user_id} is not a member of workspace {workspace_id}")
    return row["role"]


def add_member(data_dir: Path, *, workspace_id: int, user_id: int, role: str = "member") -> None:
    """Add a user to a workspace. Idempotent on existing membership
    (silently no-ops; doesn't change the role)."""
    if role not in {"admin", "member"}:
        raise ValueError(f"role must be admin|member, got {role!r}")
    conn = db.connect(data_dir)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role) VALUES (?, ?, ?)",
            (workspace_id, user_id, role),
        )
        conn.commit()
    finally:
        conn.close()


def remove_member(data_dir: Path, *, workspace_id: int, user_id: int) -> bool:
    """Drop a user from a workspace. Returns True if a row was removed.

    No "last admin protection" — V0.1 keeps it simple. Removing the
    last admin is recoverable via direct SQL by anyone with disk access,
    which is the threat model praxnest is built for (small team,
    trusted server admin).
    """
    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_member_role(data_dir: Path, *, workspace_id: int, user_id: int, role: str) -> bool:
    """Change a member's role. Returns False if they're not a member.
    Doesn't auto-add — caller checks membership first if needed."""
    if role not in {"admin", "member"}:
        raise ValueError(f"role must be admin|member, got {role!r}")
    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            "UPDATE workspace_members SET role = ? WHERE workspace_id = ? AND user_id = ?",
            (role, workspace_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_members(data_dir: Path, workspace_id: int) -> list[dict[str, Any]]:
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.role AS user_role,
                   m.role AS workspace_role, m.added_at
              FROM workspace_members m
              JOIN users u ON u.id = m.user_id
             WHERE m.workspace_id = ?
             ORDER BY m.added_at ASC
            """,
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
