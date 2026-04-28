"""Attachments — upload, serve, ACL, dedup, quota."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from praxnest import attachments as att, auth, db, workspaces


# ── Module-level (no HTTP) ─────────────────────────────────────────────────


@pytest.fixture
def workspace(data_dir):
    db.initialize(data_dir)
    user = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="proj", created_by=user)
    return {"data_dir": data_dir, "user_id": user, "workspace_id": ws}


def test_store_writes_disk_file_and_metadata(workspace):
    record = att.store(
        workspace["data_dir"],
        workspace_id=workspace["workspace_id"],
        filename="screenshot.png",
        mime_type="image/png",
        stream=io.BytesIO(b"fake png bytes"),
        uploaded_by=workspace["user_id"],
    )
    assert record.id > 0
    assert record.filename == "screenshot.png"
    assert record.mime_type == "image/png"
    assert record.size_bytes == len(b"fake png bytes")
    # File on disk under expected sharded path.
    disk = Path(workspace["data_dir"]) / "attachments" / record.sha256[:2] / record.sha256
    assert disk.exists()
    assert disk.read_bytes() == b"fake png bytes"


def test_store_dedups_identical_content(workspace):
    """Same bytes uploaded twice → one disk file, two metadata rows."""
    a = att.store(
        workspace["data_dir"], workspace_id=workspace["workspace_id"],
        filename="a.png", mime_type="image/png",
        stream=io.BytesIO(b"identical content"), uploaded_by=workspace["user_id"],
    )
    b = att.store(
        workspace["data_dir"], workspace_id=workspace["workspace_id"],
        filename="b.png", mime_type="image/png",
        stream=io.BytesIO(b"identical content"), uploaded_by=workspace["user_id"],
    )
    assert a.id != b.id
    assert a.sha256 == b.sha256
    # Only one disk file should exist for that sha.
    sharded = Path(workspace["data_dir"]) / "attachments" / a.sha256[:2]
    files = list(sharded.iterdir())
    assert len(files) == 1


def test_store_strips_path_components_from_filename(workspace):
    """Untrusted filenames must NEVER let the user write outside the
    attachments dir."""
    record = att.store(
        workspace["data_dir"], workspace_id=workspace["workspace_id"],
        filename="../../etc/passwd", mime_type="text/plain",
        stream=io.BytesIO(b"x"), uploaded_by=workspace["user_id"],
    )
    # `os.path.basename('../../etc/passwd')` → 'passwd'. Display name only.
    assert record.filename == "passwd"
    assert "/" not in record.filename and "\\" not in record.filename


def test_store_rejects_empty_file(workspace):
    with pytest.raises(ValueError, match="empty"):
        att.store(
            workspace["data_dir"], workspace_id=workspace["workspace_id"],
            filename="empty.txt", mime_type="text/plain",
            stream=io.BytesIO(b""), uploaded_by=workspace["user_id"],
        )


def test_store_rejects_oversize(workspace, monkeypatch):
    monkeypatch.setattr(att, "MAX_BYTES", 100)  # 100-byte cap for the test
    with pytest.raises(att.AttachmentTooLarge):
        att.store(
            workspace["data_dir"], workspace_id=workspace["workspace_id"],
            filename="big.bin", mime_type="application/octet-stream",
            stream=io.BytesIO(b"x" * 101), uploaded_by=workspace["user_id"],
        )


def test_store_enforces_workspace_quota(workspace, monkeypatch):
    monkeypatch.setattr(att, "MAX_TOTAL_PER_WORKSPACE_BYTES", 50)
    att.store(
        workspace["data_dir"], workspace_id=workspace["workspace_id"],
        filename="a", mime_type="text/plain",
        stream=io.BytesIO(b"x" * 30), uploaded_by=workspace["user_id"],
    )
    with pytest.raises(att.WorkspaceQuotaExceeded):
        att.store(
            workspace["data_dir"], workspace_id=workspace["workspace_id"],
            filename="b", mime_type="text/plain",
            stream=io.BytesIO(b"y" * 25), uploaded_by=workspace["user_id"],
        )


def test_is_inline_safe_for_images_and_pdfs():
    assert att.is_inline_safe("image/png") is True
    assert att.is_inline_safe("image/jpeg") is True
    assert att.is_inline_safe("application/pdf") is True
    assert att.is_inline_safe("text/plain") is True
    # HTML/SVG must NOT be inline — XSS risk if served same-origin.
    assert att.is_inline_safe("text/html") is False
    assert att.is_inline_safe("image/svg+xml") is False
    assert att.is_inline_safe("application/javascript") is False
    assert att.is_inline_safe("") is False


def test_open_disk_file_raises_when_metadata_orphaned(workspace):
    record = att.store(
        workspace["data_dir"], workspace_id=workspace["workspace_id"],
        filename="x", mime_type="text/plain",
        stream=io.BytesIO(b"x"), uploaded_by=workspace["user_id"],
    )
    # Simulate disk-side corruption: delete the on-disk file, leave row.
    disk = Path(workspace["data_dir"]) / "attachments" / record.sha256[:2] / record.sha256
    disk.unlink()
    with pytest.raises(att.AttachmentNotFound):
        att.open_disk_file(workspace["data_dir"], record.sha256)


def test_delete_removes_metadata_only(workspace):
    """After delete, metadata gone but disk file may stay (dedup
    safety). A separate housekeeping pass collects orphans — V0.3."""
    record = att.store(
        workspace["data_dir"], workspace_id=workspace["workspace_id"],
        filename="x", mime_type="text/plain",
        stream=io.BytesIO(b"hello"), uploaded_by=workspace["user_id"],
    )
    assert att.delete(workspace["data_dir"], attachment_id=record.id) is True
    # Metadata gone.
    with pytest.raises(att.AttachmentNotFound):
        att.get(workspace["data_dir"], attachment_id=record.id)
    # Disk file remains (no GC yet).
    disk = Path(workspace["data_dir"]) / "attachments" / record.sha256[:2] / record.sha256
    assert disk.exists()


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_upload_returns_url_and_md_snippet(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    files = {"file": ("login.png", b"fake", "image/png")}
    r = logged_in_client.post(f"/api/workspaces/{ws_id}/attachments", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"].startswith("/api/attachments/")
    # Image → image markdown.
    assert body["md_snippet"].startswith("![")
    assert body["md_snippet"].endswith(f"]({body['url']})")


def test_http_upload_non_image_uses_link_form(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    files = {"file": ("design.pdf", b"%PDF-1.4 fake", "application/pdf")}
    body = logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments", files=files
    ).json()
    assert body["md_snippet"].startswith("[")  # link form, not image
    assert "](" in body["md_snippet"]


def test_http_serve_returns_file_inline_for_image(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    upload = logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments",
        files={"file": ("x.png", b"png-bytes", "image/png")},
    ).json()

    r = logged_in_client.get(f"/api/attachments/{upload['id']}")
    assert r.status_code == 200
    assert r.content == b"png-bytes"
    assert r.headers["content-type"].startswith("image/png")
    assert "inline" in r.headers["content-disposition"].lower()


def test_http_serve_forces_attachment_disposition_for_html(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    upload = logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments",
        files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")},
    ).json()
    r = logged_in_client.get(f"/api/attachments/{upload['id']}")
    assert r.status_code == 200
    # HTML must NEVER render in-page — same-origin XSS via attachment.
    assert "attachment" in r.headers["content-disposition"].lower()


def test_http_serve_acl_404s_for_other_workspace(logged_in_client, data_dir):
    """Guess attachment id from another user's workspace → 404, not 403,
    not the actual file. (Mirrors notes ACL behavior.)"""
    # Other user uploads.
    other = auth.create_user(data_dir, username="other", password="hunter2hunter")
    other_ws = workspaces.create(data_dir, name="theirs", created_by=other)
    record = att.store(
        data_dir, workspace_id=other_ws,
        filename="secret.txt", mime_type="text/plain",
        stream=io.BytesIO(b"top-secret"), uploaded_by=other,
    )
    # Logged-in alice tries to fetch.
    r = logged_in_client.get(f"/api/attachments/{record.id}")
    assert r.status_code == 404
    assert b"top-secret" not in r.content


def test_http_serve_requires_auth(client, data_dir):
    """Anonymous request → 401, regardless of attachment id."""
    db.initialize(data_dir)
    user = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="x", created_by=user)
    record = att.store(
        data_dir, workspace_id=ws, filename="x", mime_type="text/plain",
        stream=io.BytesIO(b"private"), uploaded_by=user,
    )
    r = client.get(f"/api/attachments/{record.id}")
    assert r.status_code == 401
    assert b"private" not in r.content


def test_http_list_attachments(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments",
        files={"file": ("a.png", b"a", "image/png")},
    )
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments",
        files={"file": ("b.png", b"b", "image/png")},
    )
    body = logged_in_client.get(f"/api/workspaces/{ws_id}/attachments").json()
    names = [a["filename"] for a in body["attachments"]]
    assert set(names) == {"a.png", "b.png"}


def test_http_delete_attachment(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    upload = logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments",
        files={"file": ("x.png", b"x", "image/png")},
    ).json()
    r = logged_in_client.delete(f"/api/workspaces/{ws_id}/attachments/{upload['id']}")
    assert r.status_code == 200
    # 404 on next fetch.
    r = logged_in_client.get(f"/api/attachments/{upload['id']}")
    assert r.status_code == 404


def test_http_upload_too_large_returns_413(logged_in_client, monkeypatch):
    monkeypatch.setattr(att, "MAX_BYTES", 10)
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments",
        files={"file": ("big.bin", b"x" * 50, "application/octet-stream")},
    )
    assert r.status_code == 413


def test_http_upload_logged_to_audit(logged_in_client, data_dir):
    from praxnest import audit
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/attachments",
        files={"file": ("x.png", b"x", "image/png")},
    )
    actions = [r["action"] for r in audit.recent(data_dir)]
    assert "attachment.upload" in actions
