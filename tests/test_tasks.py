"""Tasks — workspace-scoped TODO with assignee/status/due."""

from __future__ import annotations

import pytest

from praxnest import auth, db, tasks, workspaces


@pytest.fixture
def workspace(data_dir):
    db.initialize(data_dir)
    user = auth.create_user(data_dir, username="alice", password="hunter2hunter")
    ws = workspaces.create(data_dir, name="proj", created_by=user)
    return {"data_dir": data_dir, "user_id": user, "ws": ws}


def test_create_task(workspace):
    t = tasks.create(
        workspace["data_dir"], workspace_id=workspace["ws"],
        title="Fix login bug", body_md="repro steps...",
        priority="high", created_by=workspace["user_id"],
    )
    assert t["id"] > 0
    assert t["status"] == "open"     # default
    assert t["priority"] == "high"


def test_create_rejects_invalid_status(workspace):
    with pytest.raises(ValueError):
        tasks.create(
            workspace["data_dir"], workspace_id=workspace["ws"],
            title="x", status="banana", created_by=workspace["user_id"],
        )


def test_create_rejects_invalid_priority(workspace):
    with pytest.raises(ValueError):
        tasks.create(
            workspace["data_dir"], workspace_id=workspace["ws"],
            title="x", priority="critical", created_by=workspace["user_id"],
        )


def test_create_rejects_empty_title(workspace):
    with pytest.raises(ValueError):
        tasks.create(
            workspace["data_dir"], workspace_id=workspace["ws"],
            title="   ", created_by=workspace["user_id"],
        )


def test_get_includes_assignee_username(workspace):
    bob = auth.create_user(workspace["data_dir"], username="bob", password="hunter2hunter")
    t = tasks.create(
        workspace["data_dir"], workspace_id=workspace["ws"],
        title="x", assignee_id=bob, created_by=workspace["user_id"],
    )
    fetched = tasks.get(workspace["data_dir"], task_id=t["id"])
    assert fetched["assignee_id"] == bob
    assert fetched["assignee_username"] == "bob"


def test_update_status_stamps_closed_at(workspace):
    t = tasks.create(
        workspace["data_dir"], workspace_id=workspace["ws"],
        title="x", created_by=workspace["user_id"],
    )
    assert t["closed_at"] is None
    updated = tasks.update(
        workspace["data_dir"], task_id=t["id"], user_id=workspace["user_id"],
        status="done",
    )
    assert updated["status"] == "done"
    assert updated["closed_at"] is not None

    # Reopening clears closed_at.
    reopened = tasks.update(
        workspace["data_dir"], task_id=t["id"], user_id=workspace["user_id"],
        status="open",
    )
    assert reopened["closed_at"] is None


def test_update_partial_fields_only(workspace):
    t = tasks.create(
        workspace["data_dir"], workspace_id=workspace["ws"],
        title="original", body_md="body", priority="low",
        created_by=workspace["user_id"],
    )
    updated = tasks.update(
        workspace["data_dir"], task_id=t["id"], user_id=workspace["user_id"],
        title="renamed",
    )
    assert updated["title"] == "renamed"
    assert updated["body_md"] == "body"      # unchanged
    assert updated["priority"] == "low"      # unchanged


def test_clear_assignee_removes_assignment(workspace):
    bob = auth.create_user(workspace["data_dir"], username="bob", password="hunter2hunter")
    t = tasks.create(
        workspace["data_dir"], workspace_id=workspace["ws"],
        title="x", assignee_id=bob, created_by=workspace["user_id"],
    )
    updated = tasks.update(
        workspace["data_dir"], task_id=t["id"], user_id=workspace["user_id"],
        clear_assignee=True,
    )
    assert updated["assignee_id"] is None


def test_list_active_above_done(workspace):
    """Done tasks should sink below active so the user's eye lands on
    open work first."""
    tasks.create(workspace["data_dir"], workspace_id=workspace["ws"],
                 title="finished", status="done", created_by=workspace["user_id"])
    tasks.create(workspace["data_dir"], workspace_id=workspace["ws"],
                 title="newest open", status="open", created_by=workspace["user_id"])
    tasks.create(workspace["data_dir"], workspace_id=workspace["ws"],
                 title="in progress", status="in_progress", created_by=workspace["user_id"])

    rows = tasks.list_for_workspace(workspace["data_dir"], workspace_id=workspace["ws"])
    titles = [r["title"] for r in rows]
    # All three present; 'finished' is last.
    assert "finished" in titles
    assert titles[-1] == "finished"


def test_list_filter_by_status(workspace):
    tasks.create(workspace["data_dir"], workspace_id=workspace["ws"],
                 title="a", status="open", created_by=workspace["user_id"])
    tasks.create(workspace["data_dir"], workspace_id=workspace["ws"],
                 title="b", status="done", created_by=workspace["user_id"])
    rows = tasks.list_for_workspace(
        workspace["data_dir"], workspace_id=workspace["ws"], status="done",
    )
    assert [r["title"] for r in rows] == ["b"]


def test_list_filter_by_assignee(workspace):
    bob = auth.create_user(workspace["data_dir"], username="bob", password="hunter2hunter")
    tasks.create(workspace["data_dir"], workspace_id=workspace["ws"],
                 title="for-alice", assignee_id=workspace["user_id"],
                 created_by=workspace["user_id"])
    tasks.create(workspace["data_dir"], workspace_id=workspace["ws"],
                 title="for-bob", assignee_id=bob, created_by=workspace["user_id"])
    rows = tasks.list_for_workspace(
        workspace["data_dir"], workspace_id=workspace["ws"], assignee_id=bob,
    )
    assert [r["title"] for r in rows] == ["for-bob"]


def test_delete_returns_false_for_unknown(workspace):
    assert tasks.delete(workspace["data_dir"], task_id=99999) is False


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_create_and_list_task(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/tasks",
        json={"title": "Fix bug", "body_md": "...", "priority": "high"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Fix bug"
    listed = logged_in_client.get(f"/api/workspaces/{ws_id}/tasks").json()["tasks"]
    assert len(listed) == 1


def test_http_assign_task_to_member(logged_in_client, data_dir):
    bob_id = auth.create_user(data_dir, username="bob", password="hunter2hunter")
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(f"/api/workspaces/{ws_id}/members", json={"username": "bob"})

    t = logged_in_client.post(
        f"/api/workspaces/{ws_id}/tasks",
        json={"title": "x", "assignee_id": bob_id},
    ).json()
    assert t["assignee_id"] == bob_id
    assert t["assignee_username"] == "bob"


def test_http_update_status_logged(logged_in_client, data_dir):
    from praxnest import audit
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    t = logged_in_client.post(
        f"/api/workspaces/{ws_id}/tasks", json={"title": "x"},
    ).json()
    logged_in_client.put(
        f"/api/workspaces/{ws_id}/tasks/{t['id']}",
        json={"status": "done"},
    )
    actions = audit.recent(data_dir)
    update_log = next(r for r in actions if r["action"] == "task.update")
    assert update_log["target"]["diff"]["status"]["from"] == "open"
    assert update_log["target"]["diff"]["status"]["to"] == "done"


def test_http_task_404_across_workspaces(logged_in_client):
    ws_a = logged_in_client.post("/api/workspaces", json={"name": "a"}).json()["id"]
    ws_b = logged_in_client.post("/api/workspaces", json={"name": "b"}).json()["id"]
    t = logged_in_client.post(
        f"/api/workspaces/{ws_a}/tasks", json={"title": "x"},
    ).json()
    r = logged_in_client.get(f"/api/workspaces/{ws_b}/tasks/{t['id']}")
    assert r.status_code == 404


def test_http_reject_invalid_status(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/tasks",
        json={"title": "x", "status": "wibble"},
    )
    assert r.status_code == 400
