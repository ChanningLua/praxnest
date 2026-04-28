"""Append-only audit log."""

from __future__ import annotations

from praxnest import audit, db


def test_log_inserts_row(data_dir):
    """Use actor_id=None for synthetic events — actor_id has a FK to
    users(id), so non-null requires a real user. Tests at this layer
    don't need to create real users; the auth flow tests below cover
    the FK-attached path."""
    db.initialize(data_dir)
    audit_id = audit.log(
        data_dir, actor_id=None, actor_username="alice",
        action="note.create", target={"note_id": 42},
    )
    assert audit_id > 0


def test_recent_returns_newest_first(data_dir):
    db.initialize(data_dir)
    for i in range(5):
        audit.log(data_dir, actor_id=None, actor_username="alice", action=f"x.{i}", target={"i": i})

    rows = audit.recent(data_dir, limit=3)
    assert len(rows) == 3
    assert rows[0]["action"] == "x.4"
    assert rows[1]["action"] == "x.3"
    assert rows[2]["action"] == "x.2"


def test_target_round_trips_unicode(data_dir):
    """Chinese in target fields must survive insert + read."""
    db.initialize(data_dir)
    audit.log(
        data_dir, actor_id=None, actor_username="李四",
        action="note.update", target={"title": "需求文档"},
    )
    rows = audit.recent(data_dir)
    assert rows[0]["actor_username"] == "李四"
    assert rows[0]["target"] == {"title": "需求文档"}


def test_login_action_lands_in_audit(client, admin_user, data_dir):
    """End-to-end: a successful login leaves an audit trail."""
    r = client.post(
        "/api/auth/login",
        json={"username": admin_user["username"], "password": admin_user["password"]},
    )
    assert r.status_code == 200
    rows = audit.recent(data_dir)
    assert any(row["action"] == "auth.login" for row in rows)


def test_logout_action_lands_in_audit(logged_in_client, admin_user, data_dir):
    logged_in_client.post("/api/auth/logout")
    rows = audit.recent(data_dir)
    actions = [row["action"] for row in rows]
    assert "auth.logout" in actions
    assert "auth.login" in actions   # login also recorded


def test_recent_limit_clamps_to_500(data_dir):
    db.initialize(data_dir)
    audit.log(data_dir, actor_id=None, actor_username="x", action="t", target={})
    rows = audit.recent(data_dir, limit=999_999)
    assert len(rows) == 1   # not 999_999, doesn't blow up


# ── HTTP layer ──────────────────────────────────────────────────────────────


def test_audit_endpoint_requires_admin(client, data_dir):
    """Non-admin members must NOT see the audit log — usernames /
    workspace names in events are sensitive."""
    from praxnest import auth as auth_lib
    auth_lib.create_user(data_dir, username="member1", password="hunter2hunter", role="member")
    client.post("/api/auth/login", json={"username": "member1", "password": "hunter2hunter"})

    r = client.get("/api/audit")
    assert r.status_code == 403


def test_audit_endpoint_works_for_admin(logged_in_client):
    """The admin user logs in (creating a login event), then queries audit."""
    r = logged_in_client.get("/api/audit")
    assert r.status_code == 200
    events = r.json()["events"]
    assert any(e["action"] == "auth.login" for e in events)


def test_audit_endpoint_anonymous_returns_401(client):
    r = client.get("/api/audit")
    assert r.status_code == 401
