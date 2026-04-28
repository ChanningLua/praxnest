"""Presence: heartbeat + workspace online roster."""

from __future__ import annotations

import pytest

from praxnest import auth, presence, workspaces


@pytest.fixture(autouse=True)
def _clean_presence():
    """Each test gets a fresh in-memory presence store."""
    presence.reset_for_tests()
    yield
    presence.reset_for_tests()


# ── presence module ────────────────────────────────────────────────────────


def test_heartbeat_then_online(monkeypatch):
    presence.heartbeat(42)
    assert presence.is_online(42) is True


def test_offline_when_no_heartbeat():
    assert presence.is_online(42) is False


def test_offline_after_window_expires(monkeypatch):
    presence.heartbeat(42)
    # Pretend it's been ages.
    far_future = pytest.approx(0)  # not used; just sanity
    assert presence.is_online(42, now=presence._LAST_SEEN[42] + 1) is True
    assert presence.is_online(42, now=presence._LAST_SEEN[42] + presence.ONLINE_WINDOW_SECONDS + 1) is False


def test_online_user_ids():
    presence.heartbeat(1)
    presence.heartbeat(2)
    presence.heartbeat(3)
    # Pretend user 2's heartbeat is stale.
    presence._LAST_SEEN[2] = presence._LAST_SEEN[2] - presence.ONLINE_WINDOW_SECONDS - 1
    online = set(presence.online_user_ids())
    assert online == {1, 3}


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_heartbeat_requires_auth(client):
    r = client.post("/api/heartbeat")
    assert r.status_code == 401


def test_heartbeat_records_presence(logged_in_client, admin_user):
    r = logged_in_client.post("/api/heartbeat")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert presence.is_online(admin_user["id"]) is True


def test_workspace_online_returns_active_members(logged_in_client, data_dir, admin_user):
    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "team"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})

    # admin heartbeats; bob doesn't
    logged_in_client.post("/api/heartbeat")

    r = logged_in_client.get(f"/api/workspaces/{ws_id}/online")
    body = r.json()
    online_usernames = {u["username"] for u in body["online"]}
    assert "admin" in online_usernames    # admin's hb just landed
    assert "bob" not in online_usernames  # bob never hb'd
    assert body["total_members"] == 2


def test_workspace_online_404_when_not_member(client, data_dir):
    """Non-member can't see who's online in someone else's workspace."""
    other = auth.create_user(data_dir, username="other", password="hunter2hunter")
    ws_id = workspaces.create(data_dir, name="theirs", created_by=other)

    auth.create_user(data_dir, username="me", password="hunter2hunter")
    client.post("/api/auth/login", json={"username": "me", "password": "hunter2hunter"})
    r = client.get(f"/api/workspaces/{ws_id}/online")
    assert r.status_code == 404
