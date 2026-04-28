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


SCHEMA_VERSION = 5


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
    server start.

    Schema migrations live as a list of incremental SQL blocks; we
    detect the current version in the db and apply any deltas above
    it. CREATE TABLE / CREATE INDEX statements are all guarded with
    IF NOT EXISTS so a fresh-start install just runs them all.
    """
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir)
    try:
        conn.executescript(SCHEMA_V1)
        # Read the persisted version (or 0 if the schema_version row
        # doesn't exist yet — old install).
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        current = int(row["version"]) if row else 0

        for to_version, ddl in MIGRATIONS:
            if current < to_version:
                conn.executescript(ddl)
                current = to_version

        conn.execute(
            "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
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


# ── Schema v2 ──────────────────────────────────────────────────────────────
# Added: attachments table (V0.2). Files are stored on disk under
# `<data-dir>/attachments/<sha256>` and metadata in this table.

SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_attachments_workspace ON attachments(workspace_id);
CREATE INDEX IF NOT EXISTS idx_attachments_sha ON attachments(sha256);
"""

# ── Schema v3 ──────────────────────────────────────────────────────────────
# Added: note_versions table — snapshot of each previous version of a
# note. On every successful note update, the OLD body+title get inserted
# here before the new version overwrites. Lets users roll back to any
# prior state without us double-storing the *current* version.

SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS note_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,        -- the version number this row REPRESENTS (the old one)
    title TEXT NOT NULL,
    body_md TEXT NOT NULL,
    saved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_versions_note ON note_versions(note_id);
"""


# ── Schema v4 ──────────────────────────────────────────────────────────────
# Added: comments + mentions tables.
# - comments: a thread under a note. Parent_id supports one-level nesting
#   (a reply to a comment); we don't allow infinite nesting because the
#   review-workflow use case doesn't need it and rendering deep trees
#   makes the right-pane scroll a mess.
# - mentions: denormalized index of "user X was @ed in comment Y" so
#   GUI can show "你被提及了 N 条" without scanning every comment body.

SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    body_md TEXT NOT NULL,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    author_username TEXT NOT NULL,           -- denormalized: survives user delete
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    edited_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_comments_note ON comments(note_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);

CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Source: which comment contained the @-mention. Always present;
    -- a future task or note-body mention could carry source_kind=note
    -- without breaking the schema.
    source_kind TEXT NOT NULL DEFAULT 'comment',
    source_id INTEGER NOT NULL,
    -- Who got mentioned + the workspace context (so the UI can list
    -- "your unread @s" scoped to a workspace).
    mentioned_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- Read-state: NULL = unread; timestamp = when user dismissed it.
    read_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_mentions_user_unread
    ON mentions(mentioned_user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_mentions_source
    ON mentions(source_kind, source_id);
"""


# ── Schema v5 ──────────────────────────────────────────────────────────────
# Added: tasks table — bug/feature/PRD tracking that needs assignee +
# status + due, distinct from notes (which are documents).
#
# Status values are lowercase keywords; we don't enforce a state-machine
# in SQL (open → in_progress → done is documented in the GUI). DB just
# accepts any string in the small set, leaving validation to Python.
#
# `related_note_id` lets a task point to its source PRD / bug-report
# note. NULL = standalone task.

SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body_md TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open'
        CHECK(status IN ('open', 'in_progress', 'blocked', 'done')),
    priority TEXT NOT NULL DEFAULT 'normal'
        CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
    assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    due_at TEXT,                                  -- ISO date or NULL
    related_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


MIGRATIONS = [
    (2, SCHEMA_V2),
    (3, SCHEMA_V3),
    (4, SCHEMA_V4),
    (5, SCHEMA_V5),
]
