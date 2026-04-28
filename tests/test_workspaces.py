"""Workspace CRUD + membership."""

from __future__ import annotations

import pytest

from praxnest import auth, db, workspaces


def test_create_inserts_workspace_and_admin_membership(data_dir):
    db.initialize(data_dir)
    user_id = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws_id = workspaces.create(data_dir, name="Engineering", created_by=user_id)
    assert ws_id > 0

    # Creator is auto-added as admin.
    members = workspaces.list_members(data_dir, ws_id)
    assert len(members) == 1
    assert members[0]["username"] == "alice"
    assert members[0]["workspace_role"] == "admin"


def test_create_rejects_empty_name(data_dir):
    db.initialize(data_dir)
    user_id = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    with pytest.raises(ValueError, match="empty"):
        workspaces.create(data_dir, name="   ", created_by=user_id)


def test_create_rejects_duplicate_name(data_dir):
    db.initialize(data_dir)
    user_id = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    workspaces.create(data_dir, name="Engineering", created_by=user_id)
    with pytest.raises(workspaces.WorkspaceAlreadyExists):
        workspaces.create(data_dir, name="Engineering", created_by=user_id)


def test_list_for_user_only_returns_member_workspaces(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_a = workspaces.create(data_dir, name="alice-only", created_by=alice)
    ws_b = workspaces.create(data_dir, name="bob-only", created_by=bob)

    a_list = workspaces.list_for_user(data_dir, alice)
    b_list = workspaces.list_for_user(data_dir, bob)

    a_names = {w["name"] for w in a_list}
    b_names = {w["name"] for w in b_list}
    assert "alice-only" in a_names
    assert "bob-only" not in a_names
    assert "bob-only" in b_names
    assert "alice-only" not in b_names


def test_assert_member_raises_for_non_member(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = workspaces.create(data_dir, name="alice-only", created_by=alice)
    with pytest.raises(workspaces.NotAMember):
        workspaces.assert_member(data_dir, workspace_id=ws_id, user_id=bob)


def test_add_member_grants_access(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = workspaces.create(data_dir, name="team", created_by=alice)

    workspaces.add_member(data_dir, workspace_id=ws_id, user_id=bob, role="member")
    role = workspaces.assert_member(data_dir, workspace_id=ws_id, user_id=bob)
    assert role == "member"


def test_add_member_idempotent(data_dir):
    """Calling add_member twice for the same user is a no-op, not an error."""
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = workspaces.create(data_dir, name="team", created_by=alice)
    workspaces.add_member(data_dir, workspace_id=ws_id, user_id=bob)
    workspaces.add_member(data_dir, workspace_id=ws_id, user_id=bob)  # no exception

    members = workspaces.list_members(data_dir, ws_id)
    assert len(members) == 2  # still just alice + bob


# ── HTTP layer ──────────────────────────────────────────────────────────────


def test_list_workspaces_requires_auth(client):
    r = client.get("/api/workspaces")
    assert r.status_code == 401


def test_create_workspace_via_http(logged_in_client):
    r = logged_in_client.post("/api/workspaces", json={"name": "Engineering"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Engineering"

    listed = logged_in_client.get("/api/workspaces").json()["workspaces"]
    assert any(w["name"] == "Engineering" for w in listed)


def test_create_workspace_rejects_duplicate(logged_in_client):
    logged_in_client.post("/api/workspaces", json={"name": "Engineering"})
    r = logged_in_client.post("/api/workspaces", json={"name": "Engineering"})
    assert r.status_code == 409


def test_get_workspace_404_when_not_member(logged_in_client, data_dir):
    """Endpoint for someone else's workspace returns 404, not 403 — that
    way you can't even confirm the workspace exists."""
    other = auth.create_user(data_dir, username="other", password="hunter2hunter")
    other_ws = workspaces.create(data_dir, name="theirs", created_by=other)

    r = logged_in_client.get(f"/api/workspaces/{other_ws}")
    assert r.status_code == 404
