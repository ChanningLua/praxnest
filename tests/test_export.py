"""Workspace export — zip contains everything, sensitive bits redacted."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from praxnest import (
    api_tokens, attachments as att, auth, comments, db,
    exporter, notes, tasks, workspaces,
)


@pytest.fixture
def populated_workspace(data_dir):
    """A workspace with notes / comments / tasks / attachments to test
    against."""
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="proj", created_by=alice)
    workspaces.add_member(data_dir, workspace_id=ws, user_id=bob)

    n1 = notes.create(data_dir, workspace_id=ws, user_id=alice,
                      title="PRD", body_md="# PRD\n\nLogin spec")
    notes.create(data_dir, workspace_id=ws, user_id=alice, folder_path="bugs",
                 title="Bug 001", body_md="login broken")

    comments.create(data_dir, note_id=n1["id"], body_md="LGTM @bob",
                    author_id=alice, author_username="alice", workspace_id=ws)

    tasks.create(data_dir, workspace_id=ws, title="Fix bug",
                 created_by=alice, assignee_id=bob)

    att.store(data_dir, workspace_id=ws, filename="screenshot.png",
              mime_type="image/png", stream=io.BytesIO(b"fake-png-bytes"),
              uploaded_by=alice)

    # Generate a token but DON'T expect it to leak in the export.
    api_tokens.create(data_dir, user_id=alice, name="ci-bot")
    return {"data_dir": data_dir, "ws": ws, "alice": alice, "bob": bob}


def test_export_zips_notes_with_folder_structure(populated_workspace):
    blob = exporter.export_workspace(
        populated_workspace["data_dir"],
        workspace_id=populated_workspace["ws"],
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    paths = set(zf.namelist())
    assert "notes/PRD.md" in paths
    assert "notes/bugs/Bug 001.md" in paths


def test_export_includes_metadata(populated_workspace):
    blob = exporter.export_workspace(
        populated_workspace["data_dir"],
        workspace_id=populated_workspace["ws"],
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    meta = json.loads(zf.read("metadata.json"))
    assert meta["workspace_name"] == "proj"
    assert meta["counts"]["notes"] == 2
    assert meta["counts"]["comments"] == 1
    assert meta["counts"]["tasks"] == 1
    assert meta["counts"]["attachments"] == 1


def test_export_note_has_frontmatter(populated_workspace):
    blob = exporter.export_workspace(
        populated_workspace["data_dir"],
        workspace_id=populated_workspace["ws"],
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    body = zf.read("notes/PRD.md").decode("utf-8")
    assert body.startswith("---\n")
    assert "praxnest_note_id:" in body
    assert "Login spec" in body


def test_export_includes_comment_with_author(populated_workspace):
    blob = exporter.export_workspace(
        populated_workspace["data_dir"],
        workspace_id=populated_workspace["ws"],
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    rows = json.loads(zf.read("comments.json"))
    assert len(rows) == 1
    assert rows[0]["author_username"] == "alice"
    assert "LGTM" in rows[0]["body_md"]


def test_export_includes_attachment_bytes(populated_workspace):
    blob = exporter.export_workspace(
        populated_workspace["data_dir"],
        workspace_id=populated_workspace["ws"],
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    idx = json.loads(zf.read("attachments-index.json"))
    assert len(idx) == 1
    zip_path = idx[0]["zip_path"]
    assert zf.read(zip_path) == b"fake-png-bytes"


def test_export_never_leaks_password_hashes(populated_workspace):
    blob = exporter.export_workspace(
        populated_workspace["data_dir"],
        workspace_id=populated_workspace["ws"],
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    # bcrypt $2b$ prefix is the canary — must not appear anywhere.
    for name in zf.namelist():
        content = zf.read(name)
        assert b"$2b$" not in content, f"bcrypt hash leaked into {name}"


def test_export_never_leaks_api_token_hash(populated_workspace):
    """Token plaintext is gone immediately, but the bcrypt hash also
    must NOT end up in the export — those tokens still grant access."""
    blob = exporter.export_workspace(
        populated_workspace["data_dir"],
        workspace_id=populated_workspace["ws"],
    )
    zf = zipfile.ZipFile(io.BytesIO(blob))
    full = b"\n".join(zf.read(n) for n in zf.namelist())
    assert b"token_hash" not in full


def test_export_filename_sanitization():
    """Forbidden chars in titles must be neutralized — otherwise
    Windows users can't unzip."""
    assert exporter._safe_filename("a/b") == "a_b"
    assert exporter._safe_filename('a"b<>c') == "a_b__c"
    assert exporter._safe_filename(".") == "untitled"
    assert exporter._safe_filename("") == "untitled"
    # Trailing dot stripped.
    assert exporter._safe_filename("foo.") == "foo"


def test_export_dedupes_same_sha_attachments(populated_workspace):
    """Two attachment rows with identical bytes → one disk file in the zip."""
    data_dir = populated_workspace["data_dir"]
    ws = populated_workspace["ws"]
    alice = populated_workspace["alice"]
    # Two more uploads of the SAME bytes.
    att.store(data_dir, workspace_id=ws, filename="copy1.png",
              mime_type="image/png", stream=io.BytesIO(b"identical"),
              uploaded_by=alice)
    att.store(data_dir, workspace_id=ws, filename="copy2.png",
              mime_type="image/png", stream=io.BytesIO(b"identical"),
              uploaded_by=alice)

    blob = exporter.export_workspace(data_dir, workspace_id=ws)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    paths = [p for p in zf.namelist() if p.startswith("attachments/")]
    # 2 disk files only (the original screenshot + dedup'd "identical" pair).
    assert len([p for p in paths if not p.endswith(".json")]) == 2
    # But both rows in the index.
    idx = json.loads(zf.read("attachments-index.json"))
    assert len(idx) == 3   # screenshot + copy1 + copy2


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_export_returns_zip(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Hello", "body_md": "world"},
    )
    r = logged_in_client.get(f"/api/workspaces/{ws_id}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"].lower()
    assert "praxnest-ws.zip" in r.headers["content-disposition"]
    # Payload is a real zip.
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "notes/Hello.md" in zf.namelist()


def test_http_export_403_for_non_admin_member(client, data_dir):
    """Workspace member without admin role can't export."""
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter", role="member")
    ws = workspaces.create(data_dir, name="proj", created_by=alice)
    workspaces.add_member(data_dir, workspace_id=ws, user_id=bob, role="member")

    client.post("/api/auth/login", json={"username": "bob", "password": "hunter2hunter"})
    r = client.get(f"/api/workspaces/{ws}/export")
    assert r.status_code == 403


def test_http_export_404_for_non_member(client, data_dir):
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    other = auth.create_user(data_dir, username="other", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="proj", created_by=alice)

    client.post("/api/auth/login", json={"username": "other", "password": "hunter2hunter"})
    r = client.get(f"/api/workspaces/{ws}/export")
    assert r.status_code == 404


def test_http_export_logged_to_audit(logged_in_client, data_dir):
    from praxnest import audit
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.get(f"/api/workspaces/{ws_id}/export")
    actions = [r["action"] for r in audit.recent(data_dir)]
    assert "workspace.export" in actions
