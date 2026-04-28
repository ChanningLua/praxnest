"""Workspace routes — list / create / members."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, auth as auth_lib, workspaces
from .auth import require_user


router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class CreateWorkspaceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class AddMemberBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: str = Field(default="member")


class SetRoleBody(BaseModel):
    role: str = Field(...)


class CreateUserBody(BaseModel):
    """Admin-only — bootstrap a new user account."""
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=512)
    role: str = Field(default="member")


def _require_workspace_admin(request: Request, workspace_id: int, user: dict) -> None:
    """Caller must be a workspace member AND have admin role in that
    workspace. Distinct from `user.role` which is the system-level role."""
    try:
        ws_role = workspaces.assert_member(
            request.app.state.data_dir, workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")
    if ws_role != "admin":
        raise HTTPException(403, "workspace admin required for this action")


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


# ── Member management (admin only) ──────────────────────────────────────────


@router.post("/{workspace_id}/members")
def add_workspace_member(
    workspace_id: int, body: AddMemberBody, request: Request,
    user=Depends(require_user),
) -> dict[str, Any]:
    """Admin invites an existing user to this workspace.

    The user must already exist in the system (created via
    `POST /api/admin/users` or `praxnest init`). Cross-checking by
    username avoids leaking the user table id space.
    """
    _require_workspace_admin(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    if body.role not in {"admin", "member"}:
        raise HTTPException(400, "role must be admin|member")

    # Resolve username → id. Returns None if no such user.
    conn = workspaces.db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT id, username FROM users WHERE username = ?", (body.username.strip(),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(404, f"no user named {body.username!r}; create the account first")

    workspaces.add_member(data_dir, workspace_id=workspace_id, user_id=row["id"], role=body.role)
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="workspace.member.add",
        target={"workspace_id": workspace_id, "added": row["username"], "role": body.role},
    )
    return {"added": True, "username": row["username"], "role": body.role}


@router.delete("/{workspace_id}/members/{member_user_id}")
def remove_workspace_member(
    workspace_id: int, member_user_id: int, request: Request,
    user=Depends(require_user),
) -> dict[str, Any]:
    _require_workspace_admin(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    if member_user_id == user["id"]:
        # Self-removal is suspicious — usually a finger-slip. Refuse;
        # admins who really want out can do it via SQL.
        raise HTTPException(400, "cannot remove yourself; ask another admin")

    removed = workspaces.remove_member(
        data_dir, workspace_id=workspace_id, user_id=member_user_id,
    )
    if not removed:
        raise HTTPException(404, "user not a member of this workspace")
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="workspace.member.remove",
        target={"workspace_id": workspace_id, "removed_user_id": member_user_id},
    )
    return {"removed": True}


@router.put("/{workspace_id}/members/{member_user_id}/role")
def set_workspace_member_role(
    workspace_id: int, member_user_id: int, body: SetRoleBody, request: Request,
    user=Depends(require_user),
) -> dict[str, Any]:
    _require_workspace_admin(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    if body.role not in {"admin", "member"}:
        raise HTTPException(400, "role must be admin|member")

    ok = workspaces.set_member_role(
        data_dir, workspace_id=workspace_id, user_id=member_user_id, role=body.role,
    )
    if not ok:
        raise HTTPException(404, "user not a member of this workspace")
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="workspace.member.role",
        target={"workspace_id": workspace_id, "user_id": member_user_id, "new_role": body.role},
    )
    return {"updated": True, "role": body.role}


# ── Admin: create new user (system-level) ───────────────────────────────────
#
# Lives on a separate `/api/admin/*` prefix because it's a system-level
# operation (creating accounts), distinct from workspace ACL changes.
# Mounted in app.py via `admin_router`.

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


@admin_router.post("/users")
def admin_create_user(
    body: CreateUserBody, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    """System-admin-only: create a new user account.

    The created user has no workspace memberships yet — call
    POST /api/workspaces/{ws}/members afterwards to add them where
    needed. Splitting account creation from membership lets a single
    user belong to many workspaces without re-creating the account.
    """
    if user.get("role") != "admin":
        raise HTTPException(403, "system admin required for this action")
    data_dir = request.app.state.data_dir
    if body.role not in {"admin", "member"}:
        raise HTTPException(400, "role must be admin|member")

    try:
        new_id = auth_lib.create_user(
            data_dir,
            username=body.username, password=body.password, role=body.role,
        )
    except auth_lib.UserAlreadyExists as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="admin.user.create",
        target={"user_id": new_id, "username": body.username, "role": body.role},
    )
    return {"id": new_id, "username": body.username, "role": body.role}


@admin_router.get("/users")
def admin_list_users(request: Request, user=Depends(require_user)) -> dict[str, Any]:
    """List all users — needed by the workspace member-add UI to
    populate a "pick existing user" dropdown without exposing the
    user table to non-admins."""
    if user.get("role") != "admin":
        raise HTTPException(403, "system admin required")
    conn = workspaces.db.connect(request.app.state.data_dir)
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()
    return {"users": [dict(r) for r in rows]}
