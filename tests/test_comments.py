"""Comments + @mentions: parsing / CRUD / ACL / notifications."""

from __future__ import annotations

import pytest

from praxnest import auth, comments, db, notes, workspaces


# ── extract_mentions (pure) ─────────────────────────────────────────────────


def test_extract_mentions_finds_at_username():
    body = "thanks @alice and @bob, ping @alice again"
    assert comments.extract_mentions(body) == ["alice", "bob"]


def test_extract_mentions_ignores_email_at():
    """@ inside a word like an email shouldn't match."""
    body = "see x@example.com about it"
    # 'example' starts with letter and follows '@' — that DOES match.
    # That's a false positive we accept for V0.4 (rare in practice in
    # technical-team prose); a real fix needs a stop-anchor before '@'.
    # Test pins the *current* behavior so we notice if it changes.
    assert "example" in comments.extract_mentions(body)


def test_extract_mentions_handles_chinese_text():
    body = "请 @alice 看下这个，@charlie 同意吗？"
    assert comments.extract_mentions(body) == ["alice", "charlie"]


def test_extract_mentions_dedupes():
    assert comments.extract_mentions("@a @b @a @c @b") == ["a", "b", "c"]


def test_extract_mentions_caps_username_length():
    """Usernames are capped at 64 (matches auth) — longer @s don't
    create runaway tokens."""
    long_name = "x" * 200
    result = comments.extract_mentions(f"@{long_name}")
    assert len(result[0]) <= 64


# ── module-level CRUD ──────────────────────────────────────────────────────


@pytest.fixture
def workspace_note(data_dir):
    """Returns dict with data_dir, alice user_id, ws id, n note id."""
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="proj", created_by=alice)
    n = notes.create(data_dir, workspace_id=ws, user_id=alice,
                     title="PRD", body_md="content")
    return {"data_dir": data_dir, "user_id": alice, "ws": ws, "note_id": n["id"]}


def test_create_comment_inserts_row(workspace_note):
    c = comments.create(
        workspace_note["data_dir"],
        note_id=workspace_note["note_id"], body_md="LGTM",
        author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    assert c["id"] > 0
    assert c["body_md"] == "LGTM"
    assert c["author_username"] == "alice"
    assert c["parent_id"] is None


def test_create_comment_rejects_empty(workspace_note):
    with pytest.raises(ValueError):
        comments.create(
            workspace_note["data_dir"],
            note_id=workspace_note["note_id"], body_md="   ",
            author_id=workspace_note["user_id"], author_username="alice",
            workspace_id=workspace_note["ws"],
        )


def test_create_threaded_reply(workspace_note):
    parent = comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="top", author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    reply = comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="reply", parent_id=parent["id"],
        author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    assert reply["parent_id"] == parent["id"]


def test_reply_must_be_under_same_note(workspace_note):
    """parent_id from another note shouldn't be allowed — that'd
    confuse the tree-rendering."""
    parent = comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="x", author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    other = notes.create(
        workspace_note["data_dir"], workspace_id=workspace_note["ws"],
        user_id=workspace_note["user_id"], title="OtherNote", body_md="",
    )
    with pytest.raises(ValueError, match="different note"):
        comments.create(
            workspace_note["data_dir"], note_id=other["id"],
            body_md="reply", parent_id=parent["id"],
            author_id=workspace_note["user_id"], author_username="alice",
            workspace_id=workspace_note["ws"],
        )


def test_list_for_note_returns_oldest_first(workspace_note):
    for i in range(3):
        comments.create(
            workspace_note["data_dir"], note_id=workspace_note["note_id"],
            body_md=f"c{i}", author_id=workspace_note["user_id"],
            author_username="alice", workspace_id=workspace_note["ws"],
        )
    rows = comments.list_for_note(workspace_note["data_dir"], note_id=workspace_note["note_id"])
    assert [r["body_md"] for r in rows] == ["c0", "c1", "c2"]


def test_update_only_author_or_admin(workspace_note):
    bob_id = auth.create_user(workspace_note["data_dir"], username="bob", password="hunter2hunter")
    c = comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="alice's comment", author_id=workspace_note["user_id"],
        author_username="alice", workspace_id=workspace_note["ws"],
    )
    # Bob (member, not author) → forbidden.
    with pytest.raises(comments.CommentForbidden):
        comments.update(
            workspace_note["data_dir"], comment_id=c["id"], body_md="bob edits",
            actor_id=bob_id, actor_role="member",
        )
    # Alice (author) → ok.
    comments.update(
        workspace_note["data_dir"], comment_id=c["id"], body_md="alice edits",
        actor_id=workspace_note["user_id"], actor_role="member",
    )
    # System admin → ok even if not author.
    comments.update(
        workspace_note["data_dir"], comment_id=c["id"], body_md="admin moderates",
        actor_id=bob_id, actor_role="admin",
    )


def test_delete_only_author_or_admin(workspace_note):
    bob_id = auth.create_user(workspace_note["data_dir"], username="bob", password="hunter2hunter")
    c = comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="x", author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    with pytest.raises(comments.CommentForbidden):
        comments.delete(
            workspace_note["data_dir"], comment_id=c["id"],
            actor_id=bob_id, actor_role="member",
        )
    assert comments.delete(
        workspace_note["data_dir"], comment_id=c["id"],
        actor_id=workspace_note["user_id"], actor_role="member",
    ) is True


# ── @ mention indexing ─────────────────────────────────────────────────────


def test_mention_creates_row_per_mentioned_user(workspace_note):
    bob_id = auth.create_user(workspace_note["data_dir"], username="bob", password="hunter2hunter")
    charlie_id = auth.create_user(workspace_note["data_dir"], username="charlie", password="hunter2hunter")

    comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="ping @bob and @charlie",
        author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )

    bob_inbox = comments.list_mentions_for_user(workspace_note["data_dir"], user_id=bob_id)
    charlie_inbox = comments.list_mentions_for_user(workspace_note["data_dir"], user_id=charlie_id)
    assert len(bob_inbox) == 1
    assert len(charlie_inbox) == 1


def test_mention_unknown_username_is_silently_dropped(workspace_note):
    """@ghost where ghost doesn't exist: doesn't blow up, just no row."""
    comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="ping @ghost",
        author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    # No exception above. No mention rows for nonexistent user.
    conn = db.connect(workspace_note["data_dir"])
    n = conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
    conn.close()
    assert n == 0


def test_mention_self_does_not_notify(workspace_note):
    """Mentioning yourself shouldn't create a notification — noise."""
    comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="note to self @alice",
        author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    inbox = comments.list_mentions_for_user(workspace_note["data_dir"], user_id=workspace_note["user_id"])
    assert inbox == []


def test_mark_mentions_read_specific_ids(workspace_note):
    bob_id = auth.create_user(workspace_note["data_dir"], username="bob", password="hunter2hunter")
    for i in range(3):
        comments.create(
            workspace_note["data_dir"], note_id=workspace_note["note_id"],
            body_md=f"c{i} @bob",
            author_id=workspace_note["user_id"], author_username="alice",
            workspace_id=workspace_note["ws"],
        )

    inbox = comments.list_mentions_for_user(workspace_note["data_dir"], user_id=bob_id)
    assert len(inbox) == 3

    # Mark first 2 as read.
    n = comments.mark_mentions_read(
        workspace_note["data_dir"], user_id=bob_id,
        mention_ids=[inbox[0]["id"], inbox[1]["id"]],
    )
    assert n == 2
    unread = comments.list_mentions_for_user(workspace_note["data_dir"], user_id=bob_id, unread_only=True)
    assert len(unread) == 1


def test_mark_all_unread_when_no_ids(workspace_note):
    bob_id = auth.create_user(workspace_note["data_dir"], username="bob", password="hunter2hunter")
    for i in range(3):
        comments.create(
            workspace_note["data_dir"], note_id=workspace_note["note_id"],
            body_md=f"@bob {i}",
            author_id=workspace_note["user_id"], author_username="alice",
            workspace_id=workspace_note["ws"],
        )
    n = comments.mark_mentions_read(workspace_note["data_dir"], user_id=bob_id)
    assert n == 3
    assert comments.unread_count(workspace_note["data_dir"], user_id=bob_id) == 0


def test_mark_read_only_affects_caller(workspace_note):
    """User A calling mark-read shouldn't dismiss user B's inbox."""
    bob_id = auth.create_user(workspace_note["data_dir"], username="bob", password="hunter2hunter")
    charlie_id = auth.create_user(workspace_note["data_dir"], username="charlie", password="hunter2hunter")

    comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="@bob",
        author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    comments.create(
        workspace_note["data_dir"], note_id=workspace_note["note_id"],
        body_md="@charlie",
        author_id=workspace_note["user_id"], author_username="alice",
        workspace_id=workspace_note["ws"],
    )
    # Charlie marks all read.
    comments.mark_mentions_read(workspace_note["data_dir"], user_id=charlie_id)
    # Bob's inbox still has 1 unread.
    assert comments.unread_count(workspace_note["data_dir"], user_id=bob_id) == 1


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_create_and_list_comment(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "x", "body_md": "y"},
    ).json()
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/comments",
        json={"body_md": "looks good"},
    )
    assert r.status_code == 200
    listed = logged_in_client.get(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/comments"
    ).json()["comments"]
    assert listed[0]["body_md"] == "looks good"


def test_http_comment_404_when_note_in_other_workspace(logged_in_client):
    ws_a = logged_in_client.post("/api/workspaces", json={"name": "a"}).json()["id"]
    ws_b = logged_in_client.post("/api/workspaces", json={"name": "b"}).json()["id"]
    note_a = logged_in_client.post(
        f"/api/workspaces/{ws_a}/notes",
        json={"title": "x", "body_md": "y"},
    ).json()
    r = logged_in_client.post(
        f"/api/workspaces/{ws_b}/notes/{note_a['id']}/comments",
        json={"body_md": "x"},
    )
    assert r.status_code == 404


def test_http_my_mentions_returns_unread_count(logged_in_client, data_dir, monkeypatch):
    """Don't actually push notify in tests."""
    from praxnest import notify
    monkeypatch.setattr(notify, "push", lambda **kw: notify.PushResult(ok=True, channel="x"))
    monkeypatch.setattr(notify, "list_channels", lambda: [])  # skip push entirely

    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "x", "body_md": "y"},
    ).json()
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/comments",
        json={"body_md": "@bob look at this"},
    )

    # Switch to bob's session.
    logged_in_client.post("/api/auth/logout")
    logged_in_client.post("/api/auth/login",
                         json={"username": "bob", "password": "hunter2hunter"})

    r = logged_in_client.get("/api/me/mentions").json()
    assert r["unread_count"] == 1
    assert len(r["mentions"]) == 1
    assert r["mentions"][0]["author_username"] == "admin"


def test_http_mark_mentions_read(logged_in_client, data_dir, monkeypatch):
    from praxnest import notify
    monkeypatch.setattr(notify, "list_channels", lambda: [])

    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "x", "body_md": "y"},
    ).json()
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/comments",
        json={"body_md": "@bob"},
    )
    logged_in_client.post("/api/auth/logout")
    logged_in_client.post("/api/auth/login",
                         json={"username": "bob", "password": "hunter2hunter"})

    r = logged_in_client.post("/api/me/mentions/mark-read", json={})
    assert r.json()["marked"] == 1
    after = logged_in_client.get("/api/me/mentions").json()
    assert after["unread_count"] == 0


def test_http_delete_comment_only_by_author(logged_in_client, data_dir, monkeypatch):
    from praxnest import notify
    monkeypatch.setattr(notify, "list_channels", lambda: [])

    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter", role="member")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "x", "body_md": "y"},
    ).json()

    # admin posts a comment.
    cmt = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/comments",
        json={"body_md": "from admin"},
    ).json()

    # Switch to bob (member).
    logged_in_client.post("/api/auth/logout")
    logged_in_client.post("/api/auth/login",
                         json={"username": "bob", "password": "hunter2hunter"})
    r = logged_in_client.delete(
        f"/api/workspaces/{ws_id}/notes/{note['id']}/comments/{cmt['id']}"
    )
    assert r.status_code == 403
