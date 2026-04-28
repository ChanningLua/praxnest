"""API tokens — long-lived machine credentials."""

from __future__ import annotations

import pytest

from praxnest import api_tokens, auth, db


@pytest.fixture
def alice(data_dir):
    db.initialize(data_dir)
    return auth.create_user(data_dir, username="alice", password="hunter2hunter")


# ── module-level ───────────────────────────────────────────────────────────


def test_create_returns_secret_only_once(data_dir, alice):
    meta, secret = api_tokens.create(data_dir, user_id=alice, name="ci-bot")
    assert secret.startswith("pnt_")
    assert meta["name"] == "ci-bot"
    assert meta["prefix"] == secret[:8]

    # The list endpoint must NEVER expose the full secret again.
    listed = api_tokens.list_for_user(data_dir, user_id=alice)
    assert "secret" not in listed[0]
    assert "token_hash" not in listed[0]
    assert listed[0]["prefix"] == secret[:8]


def test_create_rejects_empty_name(data_dir, alice):
    with pytest.raises(ValueError):
        api_tokens.create(data_dir, user_id=alice, name="")
    with pytest.raises(ValueError):
        api_tokens.create(data_dir, user_id=alice, name="   ")


def test_verify_succeeds_with_correct_secret(data_dir, alice):
    _, secret = api_tokens.create(data_dir, user_id=alice, name="ci")
    user = api_tokens.verify(data_dir, secret)
    assert user is not None
    assert user["username"] == "alice"


def test_verify_fails_with_wrong_secret(data_dir, alice):
    _, secret = api_tokens.create(data_dir, user_id=alice, name="ci")
    # Same prefix, different body — most realistic miss.
    bogus = secret[:8] + "x" * (len(secret) - 8)
    assert api_tokens.verify(data_dir, bogus) is None


def test_verify_fails_with_no_prefix(data_dir, alice):
    """Anything not starting with pnt_ is rejected outright."""
    api_tokens.create(data_dir, user_id=alice, name="ci")
    assert api_tokens.verify(data_dir, "Bearer xxx") is None
    assert api_tokens.verify(data_dir, "") is None
    assert api_tokens.verify(data_dir, "garbage") is None


def test_verify_fails_for_revoked_token(data_dir, alice):
    meta, secret = api_tokens.create(data_dir, user_id=alice, name="ci")
    assert api_tokens.revoke(data_dir, token_id=meta["id"], user_id=alice) is True
    assert api_tokens.verify(data_dir, secret) is None


def test_verify_stamps_last_used_at(data_dir, alice):
    _, secret = api_tokens.create(data_dir, user_id=alice, name="ci")
    listed = api_tokens.list_for_user(data_dir, user_id=alice)
    assert listed[0]["last_used_at"] is None

    api_tokens.verify(data_dir, secret)

    listed = api_tokens.list_for_user(data_dir, user_id=alice)
    assert listed[0]["last_used_at"] is not None


def test_revoke_only_owners_token(data_dir, alice):
    """Bob can't revoke alice's token even if he guesses the id."""
    bob = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    meta, secret = api_tokens.create(data_dir, user_id=alice, name="alice-ci")

    # Bob attempts to revoke.
    assert api_tokens.revoke(data_dir, token_id=meta["id"], user_id=bob) is False

    # Token still works for alice.
    user = api_tokens.verify(data_dir, secret)
    assert user is not None and user["username"] == "alice"


def test_revoke_unknown_id_returns_false(data_dir, alice):
    assert api_tokens.revoke(data_dir, token_id=99999, user_id=alice) is False


def test_revoke_idempotent(data_dir, alice):
    """Revoking a revoked token is harmless (returns False on the
    second call since revoked_at is already set)."""
    meta, _ = api_tokens.create(data_dir, user_id=alice, name="x")
    assert api_tokens.revoke(data_dir, token_id=meta["id"], user_id=alice) is True
    assert api_tokens.revoke(data_dir, token_id=meta["id"], user_id=alice) is False


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_create_token_returns_secret(logged_in_client):
    r = logged_in_client.post("/api/me/tokens", json={"name": "ci-bot"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "ci-bot"
    assert body["secret"].startswith("pnt_")
    assert body["prefix"] == body["secret"][:8]


def test_http_list_tokens_never_returns_secret(logged_in_client):
    logged_in_client.post("/api/me/tokens", json={"name": "x"})
    r = logged_in_client.get("/api/me/tokens").json()
    assert len(r["tokens"]) == 1
    # No leak.
    serialized = str(r)
    assert "pnt_" in r["tokens"][0]["prefix"]   # prefix yes
    assert "secret" not in r["tokens"][0]
    assert "token_hash" not in r["tokens"][0]


def test_http_revoke_token(logged_in_client):
    create = logged_in_client.post("/api/me/tokens", json={"name": "x"}).json()
    r = logged_in_client.delete(f"/api/me/tokens/{create['id']}")
    assert r.status_code == 200
    # listing now shows revoked_at set.
    listed = logged_in_client.get("/api/me/tokens").json()["tokens"]
    assert listed[0]["revoked_at"] is not None


def test_http_bearer_token_auth_works(client, data_dir):
    """Token-only requests (no session) — the actual point of API tokens."""
    auth.create_user(data_dir, username="cibot", password="hunter2hunter")
    client.post("/api/auth/login", json={"username": "cibot", "password": "hunter2hunter"})
    create = client.post("/api/me/tokens", json={"name": "ci"}).json()
    secret = create["secret"]

    # Drop the cookie session — pure header-only.
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 200
    assert r.json()["username"] == "cibot"


def test_http_bearer_with_revoked_token_401s(client, data_dir):
    auth.create_user(data_dir, username="cibot", password="hunter2hunter")
    client.post("/api/auth/login", json={"username": "cibot", "password": "hunter2hunter"})
    create = client.post("/api/me/tokens", json={"name": "ci"}).json()
    secret = create["secret"]
    client.delete(f"/api/me/tokens/{create['id']}")

    client.cookies.clear()
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 401


def test_token_create_logged_to_audit(logged_in_client, data_dir):
    from praxnest import audit as audit_lib
    logged_in_client.post("/api/me/tokens", json={"name": "ci"})
    actions = [r["action"] for r in audit_lib.recent(data_dir)]
    assert "token.create" in actions


def test_revoke_returns_404_for_other_users_token(client, data_dir):
    """Bob trying to revoke alice's token by guessing id → 404."""
    alice_id = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    meta, _ = api_tokens.create(data_dir, user_id=alice_id, name="alice-ci")

    client.post("/api/auth/login", json={"username": "bob", "password": "hunter2hunter"})
    r = client.delete(f"/api/me/tokens/{meta['id']}")
    assert r.status_code == 404
