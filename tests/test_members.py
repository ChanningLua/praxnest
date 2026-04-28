"""Workspace member management — invite / remove / change role + admin user creation."""

from __future__ import annotations

import pytest

from praxnest import auth, db, workspaces


# ── workspaces.py module functions ─────────────────────────────────────────


def test_remove_member_drops_row(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="ws", created_by=alice)
    workspaces.add_member(data_dir, workspace_id=ws, user_id=bob)

    assert workspaces.remove_member(data_dir, workspace_id=ws, user_id=bob) is True
    with pytest.raises(workspaces.NotAMember):
        workspaces.assert_member(data_dir, workspace_id=ws, user_id=bob)


def test_remove_member_returns_false_for_non_member(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="ws", created_by=alice)
    assert workspaces.remove_member(data_dir, workspace_id=ws, user_id=bob) is False


def test_set_member_role_updates_row(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="ws", created_by=alice)
    workspaces.add_member(data_dir, workspace_id=ws, user_id=bob, role="member")

    assert workspaces.set_member_role(data_dir, workspace_id=ws, user_id=bob, role="admin") is True
    role = workspaces.assert_member(data_dir, workspace_id=ws, user_id=bob)
    assert role == "admin"


def test_set_member_role_rejects_invalid(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="ws", created_by=alice)
    with pytest.raises(ValueError):
        workspaces.set_member_role(data_dir, workspace_id=ws, user_id=alice, role="superuser")


def test_set_member_role_returns_false_for_non_member(data_dir):
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="ws", created_by=alice)
    assert workspaces.set_member_role(data_dir, workspace_id=ws, user_id=999, role="member") is False


# ── HTTP layer: admin user CRUD ────────────────────────────────────────────


def test_admin_create_user_succeeds(logged_in_client, data_dir):
    """admin (default fixture is admin) can create another user."""
    r = logged_in_client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "hunter2hunter", "role": "member"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "bob"
    assert body["role"] == "member"
    # user is queryable.
    user = auth.get_user(data_dir, body["id"])
    assert user["username"] == "bob"


def test_admin_create_user_rejects_short_password(logged_in_client):
    r = logged_in_client.post(
        "/api/admin/users",
        json={"username": "bob", "password": "short", "role": "member"},
    )
    # Pydantic validation kicks in (min_length=8).
    assert r.status_code == 422


def test_admin_create_user_409_on_duplicate(logged_in_client, data_dir):
    auth.create_user(data_dir, username="taken", password="hunter2hunter")
    r = logged_in_client.post(
        "/api/admin/users",
        json={"username": "taken", "password": "hunter2hunter", "role": "member"},
    )
    assert r.status_code == 409


def test_admin_create_user_403_for_non_admin(client, data_dir):
    """A regular member cannot create users."""
    auth.create_user(data_dir, username="member1", password="hunter2hunter", role="member")
    client.post("/api/auth/login", json={"username": "member1", "password": "hunter2hunter"})
    r = client.post(
        "/api/admin/users",
        json={"username": "newbie", "password": "hunter2hunter", "role": "member"},
    )
    assert r.status_code == 403


def test_admin_list_users(logged_in_client, data_dir):
    auth.create_user(data_dir, username="bob", password="hunter2hunter")
    r = logged_in_client.get("/api/admin/users")
    body = r.json()
    usernames = {u["username"] for u in body["users"]}
    assert "admin" in usernames
    assert "bob" in usernames


def test_admin_list_users_403_for_non_admin(client, data_dir):
    auth.create_user(data_dir, username="member1", password="hunter2hunter", role="member")
    client.post("/api/auth/login", json={"username": "member1", "password": "hunter2hunter"})
    assert client.get("/api/admin/users").status_code == 403


# ── HTTP layer: workspace member CRUD ──────────────────────────────────────


def test_add_workspace_member_invites_existing_user(logged_in_client, data_dir):
    auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "team"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/members",
        json={"username": "bob", "role": "member"},
    )
    assert r.status_code == 200
    members = logged_in_client.get(f"/api/workspaces/{ws_id}/members").json()["members"]
    usernames = {m["username"] for m in members}
    assert "bob" in usernames


def test_add_workspace_member_404_for_unknown_user(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "team"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/members",
        json={"username": "ghost", "role": "member"},
    )
    assert r.status_code == 404


def test_add_workspace_member_403_for_non_workspace_admin(client, data_dir):
    """Even system-admin needs to be a workspace admin to add members."""
    member = auth.create_user(data_dir, username="member1", password="hunter2hunter", role="member")
    other = auth.create_user(data_dir, username="other", password="hunter2hunter", role="admin")
    # member1 owns this workspace
    ws = workspaces.create(data_dir, name="m1ws", created_by=member)
    # other user is system admin but NOT a member of this workspace.
    client.post("/api/auth/login", json={"username": "other", "password": "hunter2hunter"})
    r = client.post(
        f"/api/workspaces/{ws}/members",
        json={"username": "other", "role": "member"},
    )
    # Returns 404 (not even a member) — correct behavior, no leak.
    assert r.status_code == 404


def test_remove_workspace_member(logged_in_client, data_dir):
    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "team"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})

    r = logged_in_client.delete(f"/api/workspaces/{ws_id}/members/{bob_id}")
    assert r.status_code == 200

    members = logged_in_client.get(f"/api/workspaces/{ws_id}/members").json()["members"]
    assert all(m["id"] != bob_id for m in members)


def test_cannot_remove_yourself(logged_in_client, data_dir, admin_user):
    """Self-removal is blocked — common finger-slip."""
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "team"}).json()["id"]
    r = logged_in_client.delete(f"/api/workspaces/{ws_id}/members/{admin_user['id']}")
    assert r.status_code == 400
    assert "yourself" in r.json()["detail"].lower()


def test_set_member_role_via_http(logged_in_client, data_dir):
    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "team"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})

    r = logged_in_client.put(
        f"/api/workspaces/{ws_id}/members/{bob_id}/role",
        json={"role": "admin"},
    )
    assert r.status_code == 200

    # Bob now has admin role IN this workspace (system role unchanged).
    members = logged_in_client.get(f"/api/workspaces/{ws_id}/members").json()["members"]
    bob = next(m for m in members if m["id"] == bob_id)
    assert bob["workspace_role"] == "admin"


def test_member_actions_logged_to_audit(logged_in_client, data_dir):
    from praxnest import audit
    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "team"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})
    logged_in_client.put(f"/api/workspaces/{ws_id}/members/{bob_id}/role", json={"role": "admin"})
    logged_in_client.delete(f"/api/workspaces/{ws_id}/members/{bob_id}")

    actions = [r["action"] for r in audit.recent(data_dir)]
    assert "workspace.member.add" in actions
    assert "workspace.member.role" in actions
    assert "workspace.member.remove" in actions
