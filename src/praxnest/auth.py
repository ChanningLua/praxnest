"""User auth helpers: bcrypt hashing, lookup, session helpers.

Session storage is via Starlette's signed-cookie SessionMiddleware
(see ``app.py``). We only put the user id in the cookie; everything
else is fetched from the db on each request — keeps the cookie tiny
and lets us update user state (role changes, etc.) without re-login.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bcrypt

from . import db


class UserAlreadyExists(ValueError):
    pass


class AuthenticationFailed(ValueError):
    pass


def hash_password(password: str) -> str:
    """bcrypt hash, default cost 12 (~250ms on a modern machine — fine
    for login but not so cheap that brute force is comfy)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash → fail closed.
        return False


def create_user(data_dir: Path, *, username: str, password: str, role: str = "member") -> int:
    """Insert a user. Returns the new user_id. Raises UserAlreadyExists
    on duplicate username (sqlite UNIQUE violation translated)."""
    if not username or not username.strip():
        raise ValueError("username must not be empty")
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if role not in {"admin", "member"}:
        raise ValueError(f"role must be admin|member, got {role!r}")

    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username.strip(), hash_password(password), role),
        )
        conn.commit()
        return int(cur.lastrowid)
    except Exception as exc:
        # sqlite3.IntegrityError on UNIQUE violation
        msg = str(exc).lower()
        if "unique" in msg or "constraint" in msg:
            raise UserAlreadyExists(f"username {username!r} already taken") from None
        raise
    finally:
        conn.close()


def authenticate(data_dir: Path, *, username: str, password: str) -> dict[str, Any]:
    """Return user row dict on success, raise AuthenticationFailed
    otherwise. We don't distinguish "wrong username" from "wrong
    password" in the error to avoid username enumeration.
    """
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(password, row["password_hash"]):
        raise AuthenticationFailed("incorrect username or password")
    return {"id": row["id"], "username": row["username"], "role": row["role"], "created_at": row["created_at"]}


def get_user(data_dir: Path, user_id: int) -> dict[str, Any] | None:
    """Return a user dict by id, or None if not found / deleted."""
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT id, username, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"], "created_at": row["created_at"]}
    finally:
        conn.close()
