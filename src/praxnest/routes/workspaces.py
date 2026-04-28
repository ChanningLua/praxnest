"""Workspace routes — list / create / members."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class CreateWorkspaceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


@router.get("")
def list_workspaces(request: Request, user=Depends(require_user)) -> dict[str, Any]:
    rows = workspaces.list_for_user(request.app.state.data_dir, user["id"])
    return {"workspaces": rows}


@router.post("")
def create_workspace(body: CreateWorkspaceBody, request: Request, user=Depends(require_user)) -> dict[str, Any]:
    data_dir = request.app.state.data_dir
    try:
        ws_id = workspaces.create(data_dir, name=body.name, created_by=user["id"])
    except workspaces.WorkspaceAlreadyExists as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="workspace.create", target={"workspace_id": ws_id, "name": body.name},
    )
    return workspaces.get(data_dir, ws_id)


@router.get("/{workspace_id}")
def get_workspace(workspace_id: int, request: Request, user=Depends(require_user)) -> dict[str, Any]:
    data_dir = request.app.state.data_dir
    try:
        workspaces.assert_member(data_dir, workspace_id=workspace_id, user_id=user["id"])
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")

    try:
        return workspaces.get(data_dir, workspace_id)
    except workspaces.WorkspaceNotFound:
        raise HTTPException(404, "workspace not found")


@router.get("/{workspace_id}/members")
def list_workspace_members(workspace_id: int, request: Request, user=Depends(require_user)) -> dict[str, Any]:
    data_dir = request.app.state.data_dir
    try:
        workspaces.assert_member(data_dir, workspace_id=workspace_id, user_id=user["id"])
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")
    return {"members": workspaces.list_members(data_dir, workspace_id)}
