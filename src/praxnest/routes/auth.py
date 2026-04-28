"""Auth routes: login / logout / me.

All other API routes will use ``require_user(request)`` to enforce
authentication; that helper lives here so dependent routes can import
it without dragging in route registration.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import api_tokens, audit, auth as auth_lib


router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=512)


def require_user(request: Request) -> dict[str, Any]:
    """Dependency that returns the logged-in user dict or raises 401.

    Two valid auth paths:

    1. **Session cookie** — set by the login form. Reads ``user_id``
       from signed session, re-fetches user from db so role changes
       / disabled accounts take effect on the very next request.
    2. **Bearer token** — long-lived API token (V0.5+) in the
       ``Authorization: Bearer pnt_xxx`` header. Used for CI / scripts.

    Either succeeds → returns the user dict. Both fail → 401.
    """
    data_dir = request.app.state.data_dir

    # Path 1: session cookie.
    user_id = request.session.get("user_id")
    if user_id is not None:
        user = auth_lib.get_user(data_dir, int(user_id))
        if user is not None:
            return user
        request.session.clear()
        # Fall through to token path before giving up.

    # Path 2: Bearer token.
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        secret = auth_header[len("bearer "):].strip()
        user = api_tokens.verify(data_dir, secret)
        if user is not None:
            return user

    raise HTTPException(status_code=401, detail="not logged in")


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
