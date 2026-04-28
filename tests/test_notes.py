"""Notes CRUD + double-link parser + FTS5 search."""

from __future__ import annotations

import pytest

from praxnest import auth, db, notes, workspaces


@pytest.fixture
def workspace(data_dir):
    """A populated workspace + alice as the owner. Most note tests need this."""
    db.initialize(data_dir)
    alice = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws_id = workspaces.create(data_dir, name="proj", created_by=alice)
    return {"data_dir": data_dir, "user_id": alice, "workspace_id": ws_id}


def _create(workspace, **kw):
    return notes.create(workspace["data_dir"], workspace_id=workspace["workspace_id"],
                        user_id=workspace["user_id"], **kw)


# ── extract_links (pure function) ───────────────────────────────────────────


def test_extract_links_finds_wikilinks():
    body = "Reference [[Login Flow]] and [[Auth Spec]] in the doc"
    assert notes.extract_links(body) == ["Login Flow", "Auth Spec"]


def test_extract_links_dedups_in_order():
    body = "[[A]] then [[B]] then [[A]] again"
    assert notes.extract_links(body) == ["A", "B"]


def test_extract_links_ignores_brokens():
    body = "Single [bracket] and [[unclosed and []] empty"
    # `[]` is empty target, regex requires 1+ chars; `[[unclosed` has no closing.
    assert notes.extract_links(body) == []


def test_extract_links_handles_chinese():
    body = "见 [[需求文档]] 和 [[测试计划]]"
    assert notes.extract_links(body) == ["需求文档", "测试计划"]


def test_extract_links_strips_whitespace():
    body = "[[  Login Flow  ]]"
    assert notes.extract_links(body) == ["Login Flow"]


def test_extract_links_returns_empty_for_empty_body():
    assert notes.extract_links("") == []
    assert notes.extract_links(None) == []


# ── CRUD ────────────────────────────────────────────────────────────────────


def test_create_inserts_note_with_version_1(workspace):
    note = _create(workspace, title="Login Flow", body_md="# Login\n\nSteps")
    assert note["id"] > 0
    assert note["version"] == 1
    assert note["title"] == "Login Flow"
    assert note["folder_path"] == ""


def test_create_normalizes_folder_path(workspace):
    note = _create(workspace, title="X", folder_path="/foo//bar/")
    assert note["folder_path"] == "foo/bar"


def test_create_rejects_dotdot_in_folder(workspace):
    with pytest.raises(ValueError, match="\\.\\."):
        _create(workspace, title="X", folder_path="../escape")


def test_create_rejects_empty_title(workspace):
    with pytest.raises(ValueError, match="empty"):
        _create(workspace, title="   ")


def test_create_rejects_duplicate_title_in_same_folder(workspace):
    _create(workspace, title="Spec", folder_path="prd")
    with pytest.raises(notes.NoteAlreadyExists):
        _create(workspace, title="Spec", folder_path="prd")


def test_create_allows_same_title_in_different_folders(workspace):
    _create(workspace, title="Spec", folder_path="prd")
    n2 = _create(workspace, title="Spec", folder_path="bug")  # OK
    assert n2["folder_path"] == "bug"


def test_get_returns_full_note(workspace):
    n = _create(workspace, title="X", body_md="hello")
    got = notes.get(workspace["data_dir"], n["id"])
    assert got["title"] == "X"
    assert got["body_md"] == "hello"
    assert got["version"] == 1


def test_get_404_for_unknown_id(workspace):
    with pytest.raises(notes.NoteNotFound):
        notes.get(workspace["data_dir"], 99999)


def test_list_in_workspace_returns_all_ordered(workspace):
    _create(workspace, title="Z-last", folder_path="a")
    _create(workspace, title="A-first", folder_path="a")
    _create(workspace, title="root-note", folder_path="")

    listed = notes.list_in_workspace(workspace["data_dir"], workspace["workspace_id"])
    titles = [n["title"] for n in listed]
    # Empty folder ('') sorts before 'a'.
    assert titles == ["root-note", "A-first", "Z-last"]


# ── update / LWW ────────────────────────────────────────────────────────────


def test_update_increments_version(workspace):
    n = _create(workspace, title="X", body_md="v1")
    updated = notes.update(
        workspace["data_dir"], note_id=n["id"], expected_version=1,
        body_md="v2", user_id=workspace["user_id"],
    )
    assert updated["version"] == 2
    assert updated["body_md"] == "v2"


def test_update_raises_conflict_on_stale_version(workspace):
    n = _create(workspace, title="X", body_md="v1")
    notes.update(
        workspace["data_dir"], note_id=n["id"], expected_version=1,
        body_md="v2", user_id=workspace["user_id"],
    )
    # Second client's expected_version is now stale.
    with pytest.raises(notes.NoteVersionConflict) as exc_info:
        notes.update(
            workspace["data_dir"], note_id=n["id"], expected_version=1,
            body_md="my-version", user_id=workspace["user_id"],
        )
    assert exc_info.value.current_version == 2
    assert exc_info.value.current_body == "v2"


def test_update_partial_body_only(workspace):
    """Updating just body_md should preserve title."""
    n = _create(workspace, title="X", body_md="v1")
    updated = notes.update(
        workspace["data_dir"], note_id=n["id"], expected_version=1,
        body_md="v2", title=None, user_id=workspace["user_id"],
    )
    assert updated["title"] == "X"  # unchanged
    assert updated["body_md"] == "v2"


def test_delete_removes_note(workspace):
    n = _create(workspace, title="X")
    assert notes.delete(workspace["data_dir"], note_id=n["id"]) is True
    with pytest.raises(notes.NoteNotFound):
        notes.get(workspace["data_dir"], n["id"])


def test_delete_returns_false_for_unknown(workspace):
    assert notes.delete(workspace["data_dir"], note_id=99999) is False


# ── backlinks ───────────────────────────────────────────────────────────────


def test_backlinks_finds_referencing_notes(workspace):
    _create(workspace, title="Login Flow", body_md="# Login")
    _create(workspace, title="Auth Spec", body_md="See [[Login Flow]] for details.")
    _create(workspace, title="Roadmap", body_md="Q1: [[Login Flow]] hardening.")

    refs = notes.backlinks_to(
        workspace["data_dir"],
        workspace_id=workspace["workspace_id"], target_title="Login Flow",
    )
    titles = {r["title"] for r in refs}
    assert titles == {"Auth Spec", "Roadmap"}


def test_backlinks_excludes_self_reference(workspace):
    _create(workspace, title="Self", body_md="I link to [[Self]] which is me")
    refs = notes.backlinks_to(
        workspace["data_dir"],
        workspace_id=workspace["workspace_id"], target_title="Self",
    )
    assert refs == []


def test_backlinks_scoped_to_workspace(workspace, data_dir):
    """A wikilink in workspace B must NOT show up as a backlink in workspace A."""
    _create(workspace, title="Shared Title", body_md="A")
    other_ws = workspaces.create(data_dir, name="other", created_by=workspace["user_id"])
    notes.create(
        data_dir, workspace_id=other_ws, user_id=workspace["user_id"],
        title="External Ref", body_md="See [[Shared Title]]",
    )
    refs = notes.backlinks_to(
        data_dir, workspace_id=workspace["workspace_id"], target_title="Shared Title",
    )
    assert refs == []


# ── search ──────────────────────────────────────────────────────────────────


def test_search_finds_title_match(workspace):
    _create(workspace, title="Authentication design", body_md="…")
    _create(workspace, title="Unrelated", body_md="…")
    hits = notes.search(workspace["data_dir"], workspace_id=workspace["workspace_id"], query="authentication")
    titles = [h["title"] for h in hits]
    assert "Authentication design" in titles
    assert "Unrelated" not in titles


def test_search_finds_body_match(workspace):
    _create(workspace, title="A", body_md="we use bcrypt for passwords")
    hits = notes.search(workspace["data_dir"], workspace_id=workspace["workspace_id"], query="bcrypt")
    assert len(hits) == 1
    assert "<mark>bcrypt</mark>" in hits[0]["snippet"]


def test_search_scoped_to_workspace(workspace, data_dir):
    _create(workspace, title="In Ws", body_md="findme")
    other = workspaces.create(data_dir, name="other", created_by=workspace["user_id"])
    notes.create(
        data_dir, workspace_id=other, user_id=workspace["user_id"],
        title="In Other", body_md="findme",
    )
    hits = notes.search(data_dir, workspace_id=workspace["workspace_id"], query="findme")
    titles = [h["title"] for h in hits]
    assert titles == ["In Ws"]


def test_search_empty_query_returns_empty(workspace):
    _create(workspace, title="Anything", body_md="…")
    assert notes.search(workspace["data_dir"], workspace_id=workspace["workspace_id"], query="") == []
    assert notes.search(workspace["data_dir"], workspace_id=workspace["workspace_id"], query="   ") == []


def test_search_handles_special_chars_safely(workspace):
    """User typing FTS5 special chars (/, *, ", etc.) shouldn't crash."""
    _create(workspace, title="x", body_md="some text")
    # No exception, just empty or non-matching results.
    hits = notes.search(workspace["data_dir"], workspace_id=workspace["workspace_id"], query='" * /')
    assert isinstance(hits, list)


# ── HTTP layer (through routes) ─────────────────────────────────────────────


def _setup_ws_via_http(client) -> int:
    r = client.post("/api/workspaces", json={"name": "TestWs"})
    return r.json()["id"]


def test_http_create_and_list_notes(logged_in_client):
    ws_id = _setup_ws_via_http(logged_in_client)

    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Hello", "body_md": "World"},
    )
    assert r.status_code == 200, r.text
    note = r.json()
    assert note["title"] == "Hello"

    r = logged_in_client.get(f"/api/workspaces/{ws_id}/notes")
    assert any(n["title"] == "Hello" for n in r.json()["notes"])


def test_http_update_with_stale_version_returns_409(logged_in_client):
    ws_id = _setup_ws_via_http(logged_in_client)
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "X", "body_md": "v1"},
    ).json()

    # Bump to v2.
    logged_in_client.put(
        f"/api/workspaces/{ws_id}/notes/{note['id']}",
        json={"expected_version": 1, "body_md": "v2"},
    )

    # Stale request — should 409.
    r = logged_in_client.put(
        f"/api/workspaces/{ws_id}/notes/{note['id']}",
        json={"expected_version": 1, "body_md": "stale"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "version_conflict"
    assert detail["current_version"] == 2
    assert detail["current_body"] == "v2"


def test_http_get_note_from_other_workspace_404s(logged_in_client, data_dir):
    """Crossing workspaces via direct id must 404 even though the id exists."""
    ws_id = _setup_ws_via_http(logged_in_client)
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "X", "body_md": ""},
    ).json()

    # Another workspace I'm in.
    other = logged_in_client.post("/api/workspaces", json={"name": "Other"}).json()
    r = logged_in_client.get(f"/api/workspaces/{other['id']}/notes/{note['id']}")
    assert r.status_code == 404


def test_http_search_returns_hits(logged_in_client):
    ws_id = _setup_ws_via_http(logged_in_client)
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "A", "body_md": "needle in haystack"},
    )
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "B", "body_md": "no match here"},
    )
    r = logged_in_client.get(f"/api/workspaces/{ws_id}/search?q=needle")
    assert r.status_code == 200
    body = r.json()
    titles = [h["title"] for h in body["results"]]
    assert titles == ["A"]


def test_http_backlinks(logged_in_client):
    ws_id = _setup_ws_via_http(logged_in_client)
    target = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Target", "body_md": "x"},
    ).json()
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Source", "body_md": "See [[Target]]"},
    )
    r = logged_in_client.get(f"/api/workspaces/{ws_id}/notes/{target['id']}/backlinks")
    body = r.json()
    titles = [n["title"] for n in body["backlinks"]]
    assert titles == ["Source"]
