"""Task routes — workspace-scoped CRUD + filter."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import audit, tasks as tasks_lib, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces/{workspace_id}/tasks", tags=["tasks"])


class CreateTaskBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body_md: str = Field(default="", max_length=20_000)
    status: str | None = Field(default=None)
    priority: str | None = Field(default=None)
    assignee_id: int | None = Field(default=None, ge=1)
    due_at: str | None = Field(default=None, max_length=32)
    related_note_id: int | None = Field(default=None, ge=1)


class UpdateTaskBody(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    body_md: str | None = Field(default=None, max_length=20_000)
    status: str | None = None
    priority: str | None = None
    assignee_id: int | None = Field(default=None, ge=1)
    due_at: str | None = None
    clear_assignee: bool = False
    clear_due: bool = False


def _check_member(request: Request, workspace_id: int, user: dict) -> None:
    try:
        workspaces.assert_member(
            request.app.state.data_dir,
            workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")


@router.get("")
def list_tasks(
    workspace_id: int, request: Request,
    status: str | None = Query(default=None),
    assignee_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    try:
        rows = tasks_lib.list_for_workspace(
            request.app.state.data_dir, workspace_id=workspace_id,
            status=status, assignee_id=assignee_id, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"tasks": rows}


@router.post("")
def create_task(
    workspace_id: int, body: CreateTaskBody, request: Request,
    user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir
    try:
        task = tasks_lib.create(
            data_dir,
            workspace_id=workspace_id,
            title=body.title, body_md=body.body_md,
            status=body.status, priority=body.priority,
            assignee_id=body.assignee_id, due_at=body.due_at,
            related_note_id=body.related_note_id,
            created_by=user["id"],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="task.create",
        target={
            "workspace_id": workspace_id, "task_id": task["id"],
            "title": task["title"], "assignee_id": task.get("assignee_id"),
        },
    )
    return task


@router.get("/{task_id}")
def get_task(
    workspace_id: int, task_id: int, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    try:
        task = tasks_lib.get(request.app.state.data_dir, task_id=task_id)
    except tasks_lib.TaskNotFound:
        raise HTTPException(404, "task not found")
    if task["workspace_id"] != workspace_id:
        raise HTTPException(404, "task not in this workspace")
    return task


@router.put("/{task_id}")
def update_task(
    workspace_id: int, task_id: int, body: UpdateTaskBody,
    request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    try:
        existing = tasks_lib.get(data_dir, task_id=task_id)
    except tasks_lib.TaskNotFound:
        raise HTTPException(404, "task not found")
    if existing["workspace_id"] != workspace_id:
        raise HTTPException(404, "task not in this workspace")

    try:
        task = tasks_lib.update(
            data_dir, task_id=task_id, user_id=user["id"],
            title=body.title, body_md=body.body_md,
            status=body.status, priority=body.priority,
            assignee_id=body.assignee_id, due_at=body.due_at,
            clear_assignee=body.clear_assignee, clear_due=body.clear_due,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    # Capture the diff in audit so admin can reconstruct who changed
    # what (status flips are most useful here).
    diff: dict[str, Any] = {}
    for key in ("title", "status", "priority", "assignee_id", "due_at"):
        if existing.get(key) != task.get(key):
            diff[key] = {"from": existing.get(key), "to": task.get(key)}
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="task.update",
        target={"workspace_id": workspace_id, "task_id": task_id, "diff": diff},
    )
    return task


@router.delete("/{task_id}")
def delete_task(
    workspace_id: int, task_id: int, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir
    try:
        existing = tasks_lib.get(data_dir, task_id=task_id)
    except tasks_lib.TaskNotFound:
        raise HTTPException(404, "task not found")
    if existing["workspace_id"] != workspace_id:
        raise HTTPException(404, "task not in this workspace")

    tasks_lib.delete(data_dir, task_id=task_id)
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="task.delete",
        target={"workspace_id": workspace_id, "task_id": task_id, "title": existing["title"]},
    )
    return {"deleted": True, "task_id": task_id}
