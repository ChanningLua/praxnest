"""AI endpoints — sidebar Q&A + workflow triggers.

Two surface kinds:

- ``POST /api/workspaces/{ws}/ai/ask`` — free-form question (sidebar
  input). Optionally takes a ``note_id`` for "ask about this note".

- ``POST /api/workspaces/{ws}/ai/workflows/{kind}/{action}`` —
  structured workflows (PRD / bug / test-report). Each workflow module
  declares its actions; this route dispatches to the right one.

We pin ``gpt-5.4`` as the default model. Users can override via the
``X-AI-Model`` header — escape hatch, not surfaced in the UI.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit, ai, notes, workspaces
from ..workflows import get_workflow, WORKFLOWS
from .auth import require_user


router = APIRouter(prefix="/api/workspaces/{workspace_id}/ai", tags=["ai"])


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=4_000)
    # Anchor the question to a specific note's body. Optional —
    # without it, the AI gets just the user's plain question.
    note_id: int | None = Field(default=None, ge=1)


class WorkflowBody(BaseModel):
    note_id: int = Field(..., ge=1)


def _check_member(request: Request, workspace_id: int, user: dict) -> None:
    try:
        workspaces.assert_member(
            request.app.state.data_dir,
            workspace_id=workspace_id, user_id=user["id"],
        )
    except workspaces.NotAMember:
        raise HTTPException(404, "workspace not found or not a member")


def _model_from_header(x_ai_model: str | None) -> str:
    return (x_ai_model or "gpt-5.4").strip()


@router.get("/status")
def ai_status(
    workspace_id: int, request: Request, user=Depends(require_user),
) -> dict[str, Any]:
    """Quick check the GUI hits on workspace open: can we even do AI?
    Used to swap "AI 助手" buttons for an "install praxagent" prompt."""
    _check_member(request, workspace_id, user)
    return {
        "available": ai.prax_available(),
        "workflows": [w["kind"] for w in WORKFLOWS],
    }


@router.post("/ask")
def ai_ask(
    workspace_id: int,
    body: AskBody,
    request: Request,
    user=Depends(require_user),
    x_ai_model: str | None = Header(default=None, alias="X-AI-Model"),
) -> dict[str, Any]:
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    # Compose the prompt: question first, then anchor note (if any) so
    # the LLM has the user's intent before context.
    prompt_parts = [body.question.strip()]
    if body.note_id:
        try:
            note = notes.get(data_dir, body.note_id)
        except notes.NoteNotFound:
            raise HTTPException(404, "anchor note not found")
        if note["workspace_id"] != workspace_id:
            raise HTTPException(404, "anchor note not in this workspace")
        prompt_parts.append(
            f"\n\n--- 笔记《{note['title']}》正文如下 ---\n{note['body_md']}"
        )

    try:
        result = ai.run_prompt("\n".join(prompt_parts), model=_model_from_header(x_ai_model))
    except ai.PraxNotInstalled as exc:
        raise HTTPException(503, str(exc))
    except ai.AIUnavailable as exc:
        raise HTTPException(502, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action="ai.ask",
        target={"workspace_id": workspace_id, "note_id": body.note_id, "chars": len(result.output)},
    )
    return {
        "output": result.output,
        "exit_code": result.exit_code,
    }


@router.post("/workflows/{kind}/{action}")
def ai_workflow(
    workspace_id: int, kind: str, action: str,
    body: WorkflowBody, request: Request,
    user=Depends(require_user),
    x_ai_model: str | None = Header(default=None, alias="X-AI-Model"),
) -> dict[str, Any]:
    """Run a structured workflow action against a note.

    The workflow module owns the prompt template; we just route the
    HTTP call to it and forward the LLM result.
    """
    _check_member(request, workspace_id, user)
    data_dir = request.app.state.data_dir

    workflow = get_workflow(kind)
    if workflow is None:
        raise HTTPException(404, f"unknown workflow {kind!r}")
    runner = workflow["actions"].get(action)
    if runner is None:
        raise HTTPException(
            404,
            f"workflow {kind!r} has no action {action!r}; available: {list(workflow['actions'].keys())}",
        )

    try:
        note = notes.get(data_dir, body.note_id)
    except notes.NoteNotFound:
        raise HTTPException(404, "note not found")
    if note["workspace_id"] != workspace_id:
        raise HTTPException(404, "note not in this workspace")

    try:
        result = runner(note=note, model=_model_from_header(x_ai_model))
    except ai.PraxNotInstalled as exc:
        raise HTTPException(503, str(exc))
    except ai.AIUnavailable as exc:
        raise HTTPException(502, str(exc))

    audit.log(
        data_dir, actor_id=user["id"], actor_username=user["username"],
        action=f"ai.workflow.{kind}.{action}",
        target={"workspace_id": workspace_id, "note_id": body.note_id, "chars": len(result.output)},
    )
    return {"output": result.output, "kind": kind, "action": action}
