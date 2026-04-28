"""Auth: bcrypt + login flow + session lifecycle."""

from __future__ import annotations

import pytest

from praxnest import auth, db


def test_hash_password_round_trips():
    h = auth.hash_password("super-secret")
    assert h != "super-secret"
    assert auth.verify_password("super-secret", h) is True
    assert auth.verify_password("wrong", h) is False


def test_verify_password_handles_garbage_hash():
    """Defense-in-depth: a malformed hash must NEVER allow login."""
    assert auth.verify_password("anything", "not-a-bcrypt-hash") is False
    assert auth.verify_password("anything", "") is False


def test_create_user_writes_row(data_dir):
    db.initialize(data_dir)
    user_id = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    assert user_id > 0


def test_create_user_rejects_short_password(data_dir):
    db.initialize(data_dir)
    with pytest.raises(ValueError, match="at least 8"):
        auth.create_user(data_dir, username="alice", password="short")


def test_create_user_rejects_invalid_role(data_dir):
    db.initialize(data_dir)
    with pytest.raises(ValueError, match="role"):
        auth.create_user(data_dir, username="alice", password="hunter2hunter", role="superuser")


def test_create_user_rejects_duplicate_username(data_dir):
    db.initialize(data_dir)
    auth.create_user(data_dir, username="alice", password="hunter2hunter")
    with pytest.raises(auth.UserAlreadyExists):
        auth.create_user(data_dir, username="alice", password="differentpass8")


def test_authenticate_succeeds_with_correct_password(data_dir):
    db.initialize(data_dir)
    auth.create_user(data_dir, username="alice", password="hunter2hunter")
    user = auth.authenticate(data_dir, username="alice", password="hunter2hunter")
    assert user["username"] == "alice"
    assert user["role"] == "member"
    # Hash never leaks back to caller.
    assert "password_hash" not in user


def test_authenticate_fails_with_wrong_password(data_dir):
    db.initialize(data_dir)
    auth.create_user(data_dir, username="alice", password="hunter2hunter")
    with pytest.raises(auth.AuthenticationFailed):
        auth.authenticate(data_dir, username="alice", password="wrong")


def test_authenticate_fails_with_unknown_user(data_dir):
    """Same exception as wrong password — avoid username enumeration."""
    db.initialize(data_dir)
    with pytest.raises(auth.AuthenticationFailed):
        auth.authenticate(data_dir, username="nobody", password="anything12")


# ── HTTP layer ──────────────────────────────────────────────────────────────


def test_me_returns_401_when_not_logged_in(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_login_with_correct_credentials_sets_session(client, admin_user):
    r = client.post(
        "/api/auth/login",
        json={"username": admin_user["username"], "password": admin_user["password"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == admin_user["username"]
    assert body["role"] == "admin"
    # Session cookie should be set.
    assert any(c.name == "praxnest_session" for c in client.cookies.jar)


def test_login_with_wrong_password_returns_401(client, admin_user):
    r = client.post(
        "/api/auth/login",
        json={"username": admin_user["username"], "password": "definitely-wrong"},
    )
    assert r.status_code == 401
    # Generic message — never reveal whether the username exists.
    assert "incorrect" in r.json()["detail"]


def test_login_with_unknown_user_returns_same_error(client):
    """Username enumeration guard — same message + status as wrong-password."""
    r = client.post(
        "/api/auth/login",
        json={"username": "doesnotexist", "password": "anything12"},
    )
    assert r.status_code == 401
    assert "incorrect" in r.json()["detail"]


def test_me_after_login_returns_user(logged_in_client, admin_user):
    r = logged_in_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == admin_user["username"]


def test_logout_clears_session(logged_in_client):
    r = logged_in_client.post("/api/auth/logout")
    assert r.status_code == 200
    # /me should now 401.
    r = logged_in_client.get("/api/auth/me")
    assert r.status_code == 401


def test_login_rejects_empty_username(client):
    r = client.post("/api/auth/login", json={"username": "", "password": "x"})
    assert r.status_code == 422  # Pydantic validation


def test_health_endpoint_works_without_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "praxnest"
    assert "version" in body


def test_root_returns_html_login_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "praxnest" in r.text
    assert "登录" in r.text or "Login" in r.text
