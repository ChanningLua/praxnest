"""Team memory — keyword-similarity recall across workspaces."""

from __future__ import annotations

from praxnest import auth, db, memory, notes, workspaces


# ── extract_keywords ───────────────────────────────────────────────────────


def test_extract_keywords_drops_stopwords():
    kw = memory.extract_keywords("The login flow is broken when the user has no email")
    # 'the', 'is', 'when', 'has', 'no' filtered. Real terms remain.
    assert "login" in kw
    assert "flow" in kw
    assert "broken" in kw
    assert "the" not in kw
    assert "is" not in kw


def test_extract_keywords_handles_chinese():
    kw = memory.extract_keywords("登录功能在用户没有邮箱时是有 bug 的，需要重新设计认证流程。")
    # Chinese 2+ char tokens should land; "的"/"是"/"需要" stopworded out.
    assert "登录" in kw or "认证" in kw or "邮箱" in kw
    assert "的" not in kw
    assert "是" not in kw


def test_extract_keywords_returns_top_n_by_frequency():
    # 'authentication' x3, 'login' x2 → both should be in result
    text = "authentication failed. authentication retry. authentication: ok. login: yes. login: ok"
    kw = memory.extract_keywords(text, top_n=2)
    assert "authentication" in kw   # most frequent
    # second slot should be login (next most frequent over 3 chars)
    assert "login" in kw


def test_extract_keywords_returns_empty_for_empty():
    assert memory.extract_keywords("") == []
    assert memory.extract_keywords("   ") == []
    assert memory.extract_keywords("the and or") == []   # all stopwords


# ── find_similar ───────────────────────────────────────────────────────────


def _setup(data_dir):
    db.initialize(data_dir)
    user = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="proj", created_by=user)
    return user, ws


def test_find_similar_finds_keyword_overlap(data_dir):
    user, ws = _setup(data_dir)
    notes.create(data_dir, workspace_id=ws, user_id=user,
                 title="Auth Spec", body_md="authentication and bcrypt password hashing")
    notes.create(data_dir, workspace_id=ws, user_id=user,
                 title="Cron Schedule", body_md="cron jobs run daily at midnight")
    notes.create(data_dir, workspace_id=ws, user_id=user,
                 title="Roadmap", body_md="quarterly authentication review and password reset")

    # New draft: very similar to Auth Spec + Roadmap.
    results = memory.find_similar(
        data_dir, workspace_id=ws,
        body_md="bcrypt authentication password best practices", top_k=5,
    )
    titles = [r["title"] for r in results]
    assert "Auth Spec" in titles
    assert "Roadmap" in titles
    assert "Cron Schedule" not in titles  # zero keyword overlap


def test_find_similar_excludes_anchor_note(data_dir):
    """When find_similar is called from an existing note's perspective,
    that note shouldn't appear in its own similar list."""
    user, ws = _setup(data_dir)
    n1 = notes.create(data_dir, workspace_id=ws, user_id=user,
                      title="A", body_md="authentication and bcrypt")
    notes.create(data_dir, workspace_id=ws, user_id=user,
                 title="B", body_md="authentication review")
    results = memory.find_similar(
        data_dir, workspace_id=ws, body_md=n1["body_md"], exclude_note_id=n1["id"],
    )
    assert all(r["id"] != n1["id"] for r in results)


def test_find_similar_returns_empty_for_no_keywords(data_dir):
    user, ws = _setup(data_dir)
    notes.create(data_dir, workspace_id=ws, user_id=user, title="x", body_md="content")
    assert memory.find_similar(data_dir, workspace_id=ws, body_md="", top_k=5) == []
    assert memory.find_similar(data_dir, workspace_id=ws, body_md="the and or", top_k=5) == []


def test_find_similar_scoped_to_workspace(data_dir):
    user, ws = _setup(data_dir)
    other_ws = workspaces.create(data_dir, name="other", created_by=user)
    notes.create(data_dir, workspace_id=ws, user_id=user,
                 title="In Project", body_md="authentication password")
    notes.create(data_dir, workspace_id=other_ws, user_id=user,
                 title="In Other", body_md="authentication password")

    # Searching in ws must NOT find the note in other_ws.
    results = memory.find_similar(data_dir, workspace_id=ws,
                                  body_md="authentication password", top_k=10)
    titles = [r["title"] for r in results]
    assert "In Project" in titles
    assert "In Other" not in titles


def test_find_similar_across_workspaces_includes_workspace_name(data_dir):
    user, ws_a = _setup(data_dir)
    ws_b = workspaces.create(data_dir, name="other", created_by=user)
    notes.create(data_dir, workspace_id=ws_a, user_id=user,
                 title="In A", body_md="bcrypt authentication")
    notes.create(data_dir, workspace_id=ws_b, user_id=user,
                 title="In B", body_md="bcrypt password handling")

    results = memory.find_similar_across_workspaces(
        data_dir, user_id=user, body_md="bcrypt authentication password",
    )
    titles = {r["title"] for r in results}
    assert titles == {"In A", "In B"}
    # Each result carries workspace_name so the UI can show "found in proj X".
    assert all("workspace_name" in r for r in results)


def test_find_similar_across_workspaces_only_user_visible(data_dir):
    """If the user isn't a member of a workspace, those notes must NOT
    leak into cross-workspace recall."""
    user, ws_user = _setup(data_dir)
    other_user = auth.create_user(data_dir, username="other", password="hunter2hunter")
    ws_other = workspaces.create(data_dir, name="theirs", created_by=other_user)

    notes.create(data_dir, workspace_id=ws_user, user_id=user,
                 title="My note", body_md="authentication design")
    notes.create(data_dir, workspace_id=ws_other, user_id=other_user,
                 title="Their note", body_md="authentication design same words")

    results = memory.find_similar_across_workspaces(
        data_dir, user_id=user, body_md="authentication design",
    )
    titles = {r["title"] for r in results}
    assert titles == {"My note"}


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_similar_in_workspace(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    n1 = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Auth", "body_md": "bcrypt authentication and session"},
    ).json()
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Cron", "body_md": "scheduled jobs daily"},
    )
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Roadmap", "body_md": "authentication review next quarter"},
    )

    r = logged_in_client.get(f"/api/workspaces/{ws_id}/memory/similar?note_id={n1['id']}")
    body = r.json()
    titles = [r["title"] for r in body["results"]]
    assert "Roadmap" in titles
    assert "Auth" not in titles  # excludes anchor


def test_http_similar_across_workspaces(logged_in_client):
    ws1 = logged_in_client.post("/api/workspaces", json={"name": "p1"}).json()["id"]
    ws2 = logged_in_client.post("/api/workspaces", json={"name": "p2"}).json()["id"]
    n1 = logged_in_client.post(
        f"/api/workspaces/{ws1}/notes",
        json={"title": "Auth p1", "body_md": "bcrypt authentication password"},
    ).json()
    logged_in_client.post(
        f"/api/workspaces/{ws2}/notes",
        json={"title": "Auth p2", "body_md": "bcrypt authentication password"},
    )

    r = logged_in_client.get(f"/api/memory/similar-across-workspaces?note_id={n1['id']}")
    body = r.json()
    titles = [r["title"] for r in body["results"]]
    # Found in p2; anchor excluded.
    assert "Auth p2" in titles
    assert "Auth p1" not in titles
