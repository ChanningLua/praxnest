"""Append-only audit log.

Every mutation that changes user-visible state goes through here. The
table is in SQLite (so search/filter is trivial) but it's *only* ever
inserted into — no updates, no deletes from app code. If we ever need
to GDPR-anonymize, that's a deliberate manual scrub via SQL, not a
casual `DELETE`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import db


def log(
    data_dir: Path,
    *,
    actor_id: int | None,
    actor_username: str,
    action: str,
    target: dict[str, Any] | None = None,
) -> int:
    """Record one event. Returns the audit row id."""
    target_json = json.dumps(target or {}, ensure_ascii=False, separators=(",", ":"))
    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            "INSERT INTO audit (actor_id, actor_username, action, target) VALUES (?, ?, ?, ?)",
            (actor_id, actor_username, action, target_json),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def recent(data_dir: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    """Read the last ``limit`` audit rows, newest first. Used by the
    GUI's audit log view + tests."""
    limit = max(1, min(int(limit), 500))
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            "SELECT id, actor_id, actor_username, action, target, at FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            target = json.loads(r["target"])
        except (json.JSONDecodeError, TypeError):
            target = {}
        out.append({
            "id": r["id"],
            "actor_id": r["actor_id"],
            "actor_username": r["actor_username"],
            "action": r["action"],
            "target": target,
            "at": r["at"],
        })
    return out
