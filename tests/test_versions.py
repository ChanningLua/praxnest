"""Note version history + restore."""

from __future__ import annotations

import pytest

from praxnest import auth, db, notes, workspaces


@pytest.fixture
def workspace(data_dir):
    db.initialize(data_dir)
    user = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="proj", created_by=user)
    return {"data_dir": data_dir, "user_id": user, "workspace_id": ws}


def _create(workspace, **kw):
    return notes.create(
        workspace["data_dir"], workspace_id=workspace["workspace_id"],
        user_id=workspace["user_id"], **kw,
    )


def _update(workspace, *, note_id, expected_version, **kw):
    return notes.update(
        workspace["data_dir"], note_id=note_id, expected_version=expected_version,
        user_id=workspace["user_id"], **kw,
    )


# ── module functions ───────────────────────────────────────────────────────


def test_update_snapshots_old_content(workspace):
    n = _create(workspace, title="Spec", body_md="v1 body")
    _update(workspace, note_id=n["id"], expected_version=1, body_md="v2 body")
    versions = notes.list_versions(workspace["data_dir"], note_id=n["id"])
    assert len(versions) == 1
    # The snapshot represents the OLD state (before the update).
    assert versions[0]["version"] == 1
    assert versions[0]["title"] == "Spec"
    assert "v1 body" in versions[0]["body_preview"]


def test_no_version_row_for_no_op_save(workspace):
    """Saving identical content shouldn't blow up the version log."""
    n = _create(workspace, title="X", body_md="content")
    _update(workspace, note_id=n["id"], expected_version=1, body_md="content", title="X")
    assert notes.list_versions(workspace["data_dir"], note_id=n["id"]) == []
    # Live row's version stays at 1 (no change).
    assert notes.get(workspace["data_dir"], n["id"])["version"] == 1


def test_versions_listed_newest_first(workspace):
    n = _create(workspace, title="X", body_md="v1")
    _update(workspace, note_id=n["id"], expected_version=1, body_md="v2")
    _update(workspace, note_id=n["id"], expected_version=2, body_md="v3")
    _update(workspace, note_id=n["id"], expected_version=3, body_md="v4")

    versions = notes.list_versions(workspace["data_dir"], note_id=n["id"])
    assert [v["version"] for v in versions] == [3, 2, 1]


def test_get_version_returns_full_body(workspace):
    n = _create(workspace, title="X", body_md="long content that exceeds the preview cutoff " * 20)
    _update(workspace, note_id=n["id"], expected_version=1, body_md="new")
    versions = notes.list_versions(workspace["data_dir"], note_id=n["id"])
    snap = notes.get_version(workspace["data_dir"], version_id=versions[0]["id"])
    assert snap["body_md"] == "long content that exceeds the preview cutoff " * 20
    assert snap["title"] == "X"


def test_restore_brings_content_back_and_snapshots_current(workspace):
    n = _create(workspace, title="X", body_md="original")
    _update(workspace, note_id=n["id"], expected_version=1, body_md="edited")
    versions = notes.list_versions(workspace["data_dir"], note_id=n["id"])
    v1_id = versions[0]["id"]

    restored = notes.restore_version(
        workspace["data_dir"], note_id=n["id"], version_id=v1_id,
        user_id=workspace["user_id"],
    )
    assert restored["body_md"] == "original"
    # Live note advanced one more version, AND a new history row was
    # added containing the "edited" content (so the restore is itself
    # reversible).
    assert restored["version"] == 3
    versions = notes.list_versions(workspace["data_dir"], note_id=n["id"])
    assert len(versions) == 2
    bodies = [v["body_preview"] for v in versions]
    assert any("edited" in b for b in bodies)
    assert any("original" in b for b in bodies)


def test_restore_404_when_version_not_belonging_to_note(workspace):
    n1 = _create(workspace, title="A", body_md="x")
    n2 = _create(workspace, title="B", body_md="y")
    _update(workspace, note_id=n1["id"], expected_version=1, body_md="x2")
    v_id = notes.list_versions(workspace["data_dir"], note_id=n1["id"])[0]["id"]
    with pytest.raises(notes.NoteNotFound):
        notes.restore_version(
            workspace["data_dir"], note_id=n2["id"], version_id=v_id,
            user_id=workspace["user_id"],
        )


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_versions_round_trip(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "X", "body_md": "v1"},
    ).json()
    logged_in_client.put(
        f"/api/workspaces/{ws_id}/notes/{note['id']}",
        json={"expected_version": 1, "body_md": "v2"},
    )
    logged_in_client.put(
        f"/api/workspaces/{ws_id}/notes/{note['id']}",
        json={"expected_version": 2, "body_md": "v3"},
    )
    r = logged_in_client.get(f"/api/workspaces/{ws_id}/notes/{note['id']}/versions")
    body = r.json()
    assert [v["version"] for v in body["versions"]] == [2, 1]


def test_http_get_single_version(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "X", "body_md": "old"},
    ).json()
    logged_in_client.put(
        f"/api/workspaces/{ws_id}/notes/{note['id']}",
        json={"expected_version": 1, "body_md": "new"},
    )
    versions = logged_in_client.get(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/versions"
    ).json()["versions"]
    v_id = versions[0]["id"]
    r = logged_in_client.get(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/versions/{v_id}"
    )
    assert r.json()["body_md"] == "old"


def test_http_restore_endpoint(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "X", "body_md": "original"},
    ).json()
    logged_in_client.put(
        f"/api/workspaces/{ws_id}/notes/{note['id']}",
        json={"expected_version": 1, "body_md": "edited"},
    )
    versions = logged_in_client.get(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/versions"
    ).json()["versions"]
    v_id = versions[0]["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/versions/{v_id}/restore"
    )
    assert r.status_code == 200
    restored = r.json()
    assert restored["body_md"] == "original"


def test_http_version_404_across_workspaces(logged_in_client):
    ws_a = logged_in_client.post("/api/workspaces", json={"name": "a"}).json()["id"]
    ws_b = logged_in_client.post("/api/workspaces", json={"name": "b"}).json()["id"]
    note_a = logged_in_client.post(
        f"/api/workspaces/{ws_a}/notes",
        json={"title": "X", "body_md": "in a"},
    ).json()
    logged_in_client.put(
        f"/api/workspaces/{ws_a}/notes/{note_a['id']}",
        json={"expected_version": 1, "body_md": "edit"},
    )
    # Fetch versions list using the wrong workspace id.
    r = logged_in_client.get(f"/api/workspaces/{ws_b}/notes/{note_a['id']}/versions")
    assert r.status_code == 404