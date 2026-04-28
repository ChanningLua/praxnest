"""SQLite schema + connection helpers.

Plain `sqlite3` (no ORM) on purpose: the schema is small, queries are
simple, and the team-collab story doesn't benefit from SQLAlchemy's
abstractions. We get FTS5 trivially this way too.

Schema is created lazily on first connect via ``initialize(data_dir)``.
Migrations are version-stamped in ``schema_version`` table; bumping the
version + appending a migration block in ``MIGRATIONS`` is the upgrade
path. Always additive — never DROP COLUMN — to make rollback safe.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def db_path(data_dir: Path) -> Path:
    return Path(data_dir) / "praxnest.db"


def connect(data_dir: Path) -> sqlite3.Connection:
    """Open a connection. Always called per-request; sqlite3 is cheap and
    avoids the cross-thread locking gotcha that bites long-lived conns
    in async servers.
    """
    conn = sqlite3.connect(db_path(data_dir))
    conn.row_factory = sqlite3.Row
    # FK enforcement is per-connection in sqlite, default OFF.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize(data_dir: Path) -> None:
    """Create or migrate the schema. Idempotent — safe to call on every
    server start."""
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir)
    try:
        conn.executescript(SCHEMA_V1)
        # Future migrations: detect schema_version, run delta DDL.
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, ?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
    finally:
        conn.close()


# ── Schema v1 ────────────────────────────────────────────────────────────────
#
# Tables we need for Week 1:
#   users           — authentication
#   workspaces      — top-level container
#   workspace_members — who can access (admin/member)
#   notes           — markdown documents (one row per note; body in md TEXT)
#   audit           — append-only event log (mutations only)
#   schema_version  — meta
#
# notes_fts is a virtual FTS5 table mirroring `notes` for full-text search.
# We use external-content FTS so the body lives once and FTS rebuilds via
# triggers — saves disk + keeps insert/update simple.

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'member')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('admin', 'member')) DEFAULT 'member',
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- Folder path relative to workspace root, '/' separated, no leading slash.
    -- Empty string = workspace root.
    folder_path TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    body_md TEXT NOT NULL DEFAULT '',
    -- Last-write-wins versioning; client sends the version it loaded so
    -- we can detect concurrent edits and surface a conflict.
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE(workspace_id, folder_path, title)
);

CREATE INDEX IF NOT EXISTS idx_notes_workspace ON notes(workspace_id);

-- Virtual FTS5 table — populated via triggers below. Indexes title+body
-- so search hits both filename and content.
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title,
    body_md,
    content='notes',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS notes_ai_fts AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, body_md) VALUES (new.id, new.title, new.body_md);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad_fts AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body_md) VALUES('delete', old.id, old.title, old.body_md);
END;
CREATE TRIGGER IF NOT EXISTS notes_au_fts AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body_md) VALUES('delete', old.id, old.title, old.body_md);
    INSERT INTO notes_fts(rowid, title, body_md) VALUES (new.id, new.title, new.body_md);
END;

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_username TEXT NOT NULL,        -- denormalized: survives user delete
    action TEXT NOT NULL,                 -- e.g. 'note.create', 'note.update', 'login'
    -- Free-form context (workspace_id, note_id, etc.) as JSON text.
    target TEXT NOT NULL DEFAULT '{}',
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit(actor_id);
"""
