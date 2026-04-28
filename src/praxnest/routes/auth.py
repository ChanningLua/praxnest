"""Auth routes: login / logout / me.

All other API routes will use ``require_user(request)`` to enforce
authentication; that helper lives here so dependent routes can import
it without dragging in route registration.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, auth as auth_lib


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)


def require_user(request: Request) -> dict[str, Any]:
    """Dependency that returns the logged-in user dict or raises 401.

    Reads the user_id from the signed session cookie, then re-fetches
    the user from db (so role changes / disabled users take effect on
    the very next request without re-login).
    """
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="not logged in")
    user = auth_lib.get_user(request.app.state.data_dir, int(user_id))
    if user is None:
        # User was deleted while their session was alive. Clear cookie.
        request.session.clear()
        raise HTTPException(status_code=401, detail="user no longer exists")
    return user


@router.post("/login")
def login(body: LoginBody, request: Request) -> dict[str, Any]:
    data_dir = request.app.state.data_dir
    try:
        user = auth_lib.authenticate(data_dir, username=body.username, password=body.password)
    except auth_lib.AuthenticationFailed as exc:
        # Generic message — never reveal whether the username exists.
        raise HTTPException(status_code=401, detail="incorrect username or password") from exc

    request.session["user_id"] = user["id"]
    audit.log(
        data_dir,
        actor_id=user["id"],
        actor_username=user["username"],
        action="auth.login",
        target={},
    )
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


@router.post("/logout")
def logout(request: Request) -> dict[str, bool]:
    user_id = request.session.get("user_id")
    if user_id is not None:
        # Best-effort lookup so we can attribute the logout event.
        user = auth_lib.get_user(request.app.state.data_dir, int(user_id))
        if user:
            audit.log(
                request.app.state.data_dir,
                actor_id=user["id"],
                actor_username=user["username"],
                action="auth.logout",
                target={},
            )
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    """Return the current user. 401 if not logged in.

    The frontend hits this on page load to decide login-page-vs-app.
    """
    return require_user(request)
