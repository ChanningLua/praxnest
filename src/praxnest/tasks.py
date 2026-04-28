"""Tasks — workspace-scoped TODO items with assignee / status / due.

Distinct from notes (which are documents). A task can optionally
``related_note_id`` back to a PRD / bug-report note for context.

V0.4 keeps the model deliberately thin — no labels, no sprint
concepts, no parent/child task hierarchy. We're not trying to
replace Linear; we just want bug/PRD-derived TODOs to live next to
their source documents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db


VALID_STATUSES = ("open", "in_progress", "blocked", "done")
VALID_PRIORITIES = ("low", "normal", "high", "urgent")


class TaskNotFound(LookupError):
    pass


def _validate_status(status: str | None) -> str:
    if status is None:
        return "open"
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    return status


def _validate_priority(priority: str | None) -> str:
    if priority is None:
        return "normal"
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"priority must be one of {VALID_PRIORITIES}, got {priority!r}")
    return priority


def create(
    data_dir: Path,
    *,
    workspace_id: int,
    title: str,
    body_md: str = "",
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    due_at: str | None = None,
    related_note_id: int | None = None,
    created_by: int,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > 200:
        raise ValueError("title max 200 chars")

    status = _validate_status(status)
    priority = _validate_priority(priority)

    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            """
            INSERT INTO tasks
                (workspace_id, title, body_md, status, priority, assignee_id,
                 due_at, related_note_id, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, title, body_md, status, priority,
             assignee_id, due_at, related_note_id, created_by, created_by),
        )
        conn.commit()
        return get(data_dir, task_id=int(cur.lastrowid))
    finally:
        conn.close()


def get(data_dir: Path, *, task_id: int) -> dict[str, Any]:
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            """
            SELECT t.id, t.workspace_id, t.title, t.body_md, t.status, t.priority,
                   t.assignee_id, ua.username AS assignee_username,
                   t.due_at, t.related_note_id, n.title AS related_note_title,
                   t.created_at, t.created_by, uc.username AS created_by_username,
                   t.updated_at, t.updated_by, t.closed_at
              FROM tasks t
              LEFT JOIN users ua ON ua.id = t.assignee_id
              LEFT JOIN users uc ON uc.id = t.created_by
              LEFT JOIN notes n ON n.id = t.related_note_id
             WHERE t.id = ?
            """,
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise TaskNotFound(f"task {task_id} not found")
    return dict(row)


def list_for_workspace(
    data_dir: Path,
    *,
    workspace_id: int,
    status: str | None = None,
    assignee_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List tasks with optional filters. Returns open/in_progress
    above done by default (active work first), then within each
    bucket newest first.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}")

    sql_parts = [
        """
        SELECT t.id, t.workspace_id, t.title,
               substr(t.body_md, 1, 200) AS body_preview,
               t.status, t.priority,
               t.assignee_id, ua.username AS assignee_username,
               t.due_at, t.related_note_id, n.title AS related_note_title,
               t.created_at
          FROM tasks t
          LEFT JOIN users ua ON ua.id = t.assignee_id
          LEFT JOIN notes n ON n.id = t.related_note_id
         WHERE t.workspace_id = ?
        """
    ]
    params: list[Any] = [workspace_id]
    if status is not None:
        sql_parts.append("AND t.status = ?")
        params.append(status)
    if assignee_id is not None:
        sql_parts.append("AND t.assignee_id = ?")
        params.append(assignee_id)
    # Active first (open/in_progress/blocked < done), then by id desc.
    sql_parts.append(
        """
        ORDER BY CASE t.status WHEN 'done' THEN 1 ELSE 0 END ASC,
                 t.id DESC
         LIMIT ?
        """
    )
    params.append(max(1, min(int(limit), 500)))

    conn = db.connect(data_dir)
    try:
        rows = conn.execute(" ".join(sql_parts), params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def update(
    data_dir: Path,
    *,
    task_id: int,
    user_id: int,
    title: str | None = None,
    body_md: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    due_at: str | None = None,
    clear_assignee: bool = False,
    clear_due: bool = False,
) -> dict[str, Any]:
    """Patch a task. Only provided fields change.

    `clear_assignee=True` and `clear_due=True` flags are how callers
    explicitly NULL out those fields (since "assignee_id=None" alone
    is ambiguous with "leave unchanged").
    """
    sets: list[str] = []
    params: list[Any] = []

    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("title must not be empty")
        sets.append("title = ?"); params.append(title)
    if body_md is not None:
        sets.append("body_md = ?"); params.append(body_md)
    if status is not None:
        _validate_status(status)
        sets.append("status = ?"); params.append(status)
        # When transitioning to / from done, stamp closed_at.
        if status == "done":
            sets.append("closed_at = COALESCE(closed_at, datetime('now'))")
        else:
            sets.append("closed_at = NULL")
    if priority is not None:
        _validate_priority(priority)
        sets.append("priority = ?"); params.append(priority)
    if assignee_id is not None:
        sets.append("assignee_id = ?"); params.append(assignee_id)
    elif clear_assignee:
        sets.append("assignee_id = NULL")
    if due_at is not None:
        sets.append("due_at = ?"); params.append(due_at)
    elif clear_due:
        sets.append("due_at = NULL")

    if not sets:
        # Idempotent no-op.
        return get(data_dir, task_id=task_id)

    sets.append("updated_at = datetime('now')")
    sets.append("updated_by = ?"); params.append(user_id)
    params.append(task_id)

    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        if cur.rowcount == 0:
            raise TaskNotFound(f"task {task_id} not found")
        conn.commit()
    finally:
        conn.close()
    return get(data_dir, task_id=task_id)


def delete(data_dir: Path, *, task_id: int) -> bool:
    conn = db.connect(data_dir)
    try:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
