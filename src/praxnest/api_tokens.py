"""API tokens — long-lived machine credentials.

Token format: ``pnt_<24-char-base32>`` (Pn=PraxNest Token). The leading
8 chars (``pnt_xxxxx``) get persisted as ``token_prefix`` so users can
identify a token in the UI without us having to display the whole
secret. The full secret is bcrypt-hashed on the way in; we never see
plaintext after creation.

Verification on incoming Bearer token: lookup by prefix (cheap), then
bcrypt-verify the rest. No table-scan, no timing leak from
not-found-vs-wrong-password (we always do one bcrypt check, even if
the prefix didn't match).
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import bcrypt

from . import db


TOKEN_PREFIX_LITERAL = "pnt_"
PREFIX_LEN = len(TOKEN_PREFIX_LITERAL) + 4   # "pnt_" + 4 chars = 8 total
TOKEN_BODY_LEN = 32                           # base32 chars after the literal prefix


# A pre-computed bcrypt hash of a random throwaway value, used in
# constant-time mismatch fallback so token-not-found doesn't run faster
# than wrong-secret. Computed lazily so import stays cheap.
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = bcrypt.hashpw(b"never-matches", bcrypt.gensalt()).decode("utf-8")
    return _DUMMY_HASH


class TokenNotFound(LookupError):
    pass


class TokenForbidden(PermissionError):
    pass


def _generate_secret() -> str:
    body = secrets.token_urlsafe(TOKEN_BODY_LEN)[:TOKEN_BODY_LEN]
    return TOKEN_PREFIX_LITERAL + body


def _prefix_of(secret: str) -> str:
    return secret[:PREFIX_LEN]


def create(
    data_dir: Path, *, user_id: int, name: str,
) -> tuple[dict[str, Any], str]:
    """Create a new token. Returns ``(metadata, secret)``; the secret
    is plaintext shown ONCE to the user and never persisted.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("token name must not be empty")
    if len(name) > 64:
        raise ValueError("token name max 64 chars")

    secret = _generate_secret()
    prefix = _prefix_of(secret)
    token_hash = bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            """
            INSERT INTO api_tokens (user_id, name, token_hash, token_prefix)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, name, token_hash, prefix),
        )
        token_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    return {
        "id": token_id,
        "name": name,
        "prefix": prefix,
        "created_at": _fetch_created_at(data_dir, token_id),
        "last_used_at": None,
        "revoked_at": None,
    }, secret


def _fetch_created_at(data_dir: Path, token_id: int) -> str:
    conn = db.connect(data_dir)
    try:
        row = conn.execute(
            "SELECT created_at FROM api_tokens WHERE id = ?", (token_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["created_at"] if row else ""


def list_for_user(data_dir: Path, *, user_id: int) -> list[dict[str, Any]]:
    """Return token metadata (no hash, no secret) for this user. Active
    + revoked both shown so the user can audit history."""
    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT id, name, token_prefix AS prefix,
                   created_at, last_used_at, revoked_at
              FROM api_tokens WHERE user_id = ?
             ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def revoke(data_dir: Path, *, token_id: int, user_id: int) -> bool:
    """Stamp ``revoked_at = now`` so subsequent lookups reject. Soft-
    revocation (we keep the row for audit) — revoked tokens never
    re-validate.

    Returns False if the token doesn't exist OR doesn't belong to this
    user; we don't 404-vs-403 distinguish to avoid token-id enumeration.
    """
    conn = db.connect(data_dir)
    try:
        cur = conn.execute(
            """
            UPDATE api_tokens
               SET revoked_at = datetime('now')
             WHERE id = ? AND user_id = ? AND revoked_at IS NULL
            """,
            (token_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def verify(data_dir: Path, presented_secret: str) -> dict[str, Any] | None:
    """Look up a presented Bearer token. Returns the user dict on
    success, None on any failure mode. Constant-time on miss-vs-hit
    (always does one bcrypt check).
    """
    if not presented_secret or not presented_secret.startswith(TOKEN_PREFIX_LITERAL):
        # Still do a dummy bcrypt check to keep timing constant.
        bcrypt.checkpw(b"x", _dummy_hash().encode("utf-8"))
        return None
    prefix = _prefix_of(presented_secret)

    conn = db.connect(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.user_id, t.token_hash, t.revoked_at,
                   u.username, u.role
              FROM api_tokens t
              JOIN users u ON u.id = t.user_id
             WHERE t.token_prefix = ?
            """,
            (prefix,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        bcrypt.checkpw(b"x", _dummy_hash().encode("utf-8"))
        return None

    matched_token_id: int | None = None
    matched_user: dict[str, Any] | None = None
    for row in rows:
        if row["revoked_at"] is not None:
            continue
        try:
            ok = bcrypt.checkpw(
                presented_secret.encode("utf-8"),
                row["token_hash"].encode("utf-8"),
            )
        except (ValueError, TypeError):
            ok = False
        if ok:
            matched_token_id = row["id"]
            matched_user = {
                "id": row["user_id"], "username": row["username"], "role": row["role"],
            }
            break

    if matched_user is None:
        # Equalize timing — even if no rows-with-this-prefix matched,
        # the loop above ran ≥1 bcrypt; nothing extra needed here.
        return None

    # Stamp last_used_at. Best-effort; ignore failures (a busy db won't
    # block auth).
    try:
        conn = db.connect(data_dir)
        try:
            conn.execute(
                "UPDATE api_tokens SET last_used_at = datetime('now') WHERE id = ?",
                (matched_token_id,),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

    return matched_user
