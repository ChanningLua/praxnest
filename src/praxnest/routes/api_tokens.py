"""API token management — `/api/me/tokens`.

Tokens are user-scoped, not workspace-scoped — they grant the same
access the issuing user has. Useful for CI / scripts. Audit log
captures creation and revocation; revoked tokens stay in the table
for history.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import api_tokens as tokens_lib, audit
from .auth import require_user


router = APIRouter(prefix="/api/me/tokens", tags=["tokens"])


class CreateTokenBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


@router.get("")
def list_my_tokens(request: Request, user=Depends(require_user)) -> dict[str, Any]:
    """Token metadata only — never returns the secret. Includes revoked
    tokens so the user can see audit history."""
    return {"tokens": tokens_lib.list_for_user(request.app.state.data_dir, user_id=user["id"])}


@router.post("")
def create_my_token(
    body: CreateTokenBody, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    """Create a new API token. The plaintext secret is in the response
    body and is the ONLY time it's ever shown — server stores only a
    bcrypt hash. Lose the secret = revoke + create new."""
    data_dir = request.app.state.data_dir
    try:
        meta, secret = tokens_lib.create(data_dir, user_id=user["id"], name=body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="token.create",
        target={"token_id": meta["id"], "name": meta["name"], "prefix": meta["prefix"]},
    )

    # Caller's response includes both the metadata + the only chance
    # they have to see the secret.
    return {**meta, "secret": secret}


@router.delete("/{token_id}")
def revoke_my_token(
    token_id: int, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    """Soft-revoke. Subsequent requests with this token return 401."""
    data_dir = request.app.state.data_dir
    revoked = tokens_lib.revoke(data_dir, token_id=token_id, user_id=user["id"])
    if not revoked:
        # Either id doesn't exist OR isn't ours OR already revoked —
        # don't distinguish (avoid token-id enumeration).
        raise HTTPException(404, "token not found")
    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="token.revoke",
        target={"token_id": token_id},
    )
    return {"revoked": True, "token_id": token_id}
