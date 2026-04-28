"""AI integration: subprocess wrapper + ask + workflow routes.

We never invoke real `prax` in tests — everything goes through a
mocked ``subprocess.run`` so failures here are about our wrapper /
routing logic, not about whether prax is installed today.
"""

from __future__ import annotations

import subprocess as _subprocess

import pytest

from praxnest import ai
from praxnest.workflows import bug, prd, test_report


# ── client.py ───────────────────────────────────────────────────────────────


def test_run_prompt_raises_if_prax_missing(monkeypatch):
    monkeypatch.setattr(ai.client.shutil, "which", lambda _: None)
    with pytest.raises(ai.PraxNotInstalled):
        ai.run_prompt("hello")


def test_run_prompt_rejects_empty(monkeypatch):
    monkeypatch.setattr(ai.client.shutil, "which", lambda _: "/fake/prax")
    with pytest.raises(ValueError, match="empty"):
        ai.run_prompt("   ")


def test_run_prompt_strips_prax_log_lines(monkeypatch):
    """The output should not contain `[prax] ...` status lines —
    those are inline-on-stdout from the prax CLI, not the LLM's reply."""
    class _FakeProc:
        returncode = 0
        stdout = (
            "\x1b[90m[prax] model=gpt-5.4 cwd=/x\x1b[0m\n"
            "Real LLM answer line 1\n"
            "[prax] attempt=1 model=gpt-5.4\n"
            "Real LLM answer line 2"
        )
        stderr = ""

    monkeypatch.setattr(ai.client.shutil, "which", lambda _: "/fake/prax")
    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _FakeProc())

    result = ai.run_prompt("hi")
    assert "Real LLM answer line 1" in result.output
    assert "Real LLM answer line 2" in result.output
    assert "[prax]" not in result.output


def test_run_prompt_passes_model_flag(monkeypatch):
    captured = {}

    class _FakeProc:
        returncode = 0; stdout = "ok"; stderr = ""

    monkeypatch.setattr(ai.client.shutil, "which", lambda _: "/fake/prax")
    def _capture(argv, *a, **kw):
        captured["argv"] = argv
        return _FakeProc()
    monkeypatch.setattr(_subprocess, "run", _capture)

    ai.run_prompt("hello", model="gpt-5.4")
    assert "--model" in captured["argv"]
    assert "gpt-5.4" in captured["argv"]
    # Always pinned to read-only — workflows are NOT supposed to edit anything.
    assert "--permission-mode" in captured["argv"]
    assert "read-only" in captured["argv"]


def test_run_prompt_handles_timeout(monkeypatch):
    monkeypatch.setattr(ai.client.shutil, "which", lambda _: "/fake/prax")
    def _timeout(*a, **kw): raise _subprocess.TimeoutExpired(cmd="prax", timeout=1)
    monkeypatch.setattr(_subprocess, "run", _timeout)

    with pytest.raises(ai.AIUnavailable, match="timed out"):
        ai.run_prompt("hi", timeout=1)


def test_prax_available_reflects_path(monkeypatch):
    monkeypatch.setattr(ai.client.shutil, "which", lambda _: "/fake/prax")
    assert ai.prax_available() is True
    monkeypatch.setattr(ai.client.shutil, "which", lambda _: None)
    assert ai.prax_available() is False


# ── workflows: just verify they call run_prompt with non-empty body ─────────


@pytest.fixture
def fake_run_prompt(monkeypatch):
    captured = {}

    def _fake(prompt, *, model=None, **kw):
        captured["prompt"] = prompt
        captured["model"] = model
        return ai.PromptResult(output="(fake reply)", exit_code=0, stderr="")

    monkeypatch.setattr(ai, "run_prompt", _fake)
    return captured


def test_prd_test_cases_passes_note_body(fake_run_prompt):
    note = {"title": "Login PRD", "body_md": "用户必须能用邮箱+密码登录"}
    result = prd.gen_test_cases(note=note, model="gpt-5.4")
    assert result.output == "(fake reply)"
    assert "Login PRD" in fake_run_prompt["prompt"]
    assert "用户必须能用邮箱+密码登录" in fake_run_prompt["prompt"]
    assert fake_run_prompt["model"] == "gpt-5.4"


def test_prd_actions_refuse_empty_body(fake_run_prompt):
    note = {"title": "X", "body_md": "   "}
    with pytest.raises(ai.AIUnavailable, match="正文为空"):
        prd.gen_test_cases(note=note, model="gpt-5.4")
    with pytest.raises(ai.AIUnavailable):
        prd.extract_requirements(note=note, model="gpt-5.4")
    with pytest.raises(ai.AIUnavailable):
        prd.acceptance_checklist(note=note, model="gpt-5.4")


def test_bug_assess_includes_severity_section(fake_run_prompt):
    note = {"title": "Login broken", "body_md": "点登录按钮没反应"}
    bug.assess(note=note, model="gpt-5.4")
    assert "严重度" in fake_run_prompt["prompt"]
    assert "P0" in fake_run_prompt["prompt"]
    assert "点登录按钮没反应" in fake_run_prompt["prompt"]


def test_test_report_summary_includes_wechat_format(fake_run_prompt):
    note = {"title": "Sprint 5 Test Run", "body_md": "passed: 38\nfailed: 4\n..."}
    test_report.summary(note=note, model="gpt-5.4")
    assert "企业微信" in fake_run_prompt["prompt"] or "飞书" in fake_run_prompt["prompt"]


# ── HTTP layer: /api/.../ai/* ────────────────────────────────────────────────


_WS_COUNTER = {"n": 0}


def _setup_ws_with_note(client, body_md="some content"):
    """Each call gets a unique workspace name (counter-suffixed) so the
    helper can be invoked multiple times in one test."""
    _WS_COUNTER["n"] += 1
    ws_id = client.post("/api/workspaces", json={"name": f"ws-{_WS_COUNTER['n']}"}).json()["id"]
    note = client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": f"T-{_WS_COUNTER['n']}", "body_md": body_md},
    ).json()
    return ws_id, note["id"]


def test_ai_status_returns_workflows(logged_in_client, monkeypatch):
    monkeypatch.setattr(ai, "prax_available", lambda: True)
    ws_id, _ = _setup_ws_with_note(logged_in_client)
    r = logged_in_client.get(f"/api/workspaces/{ws_id}/ai/status")
    body = r.json()
    assert body["available"] is True
    assert "prd" in body["workflows"]
    assert "bug" in body["workflows"]


def test_ai_status_when_prax_missing(logged_in_client, monkeypatch):
    monkeypatch.setattr(ai, "prax_available", lambda: False)
    ws_id, _ = _setup_ws_with_note(logged_in_client)
    r = logged_in_client.get(f"/api/workspaces/{ws_id}/ai/status")
    assert r.json()["available"] is False


def test_ai_ask_returns_503_when_prax_missing(logged_in_client, monkeypatch):
    def _fake(*a, **kw): raise ai.PraxNotInstalled("nope")
    monkeypatch.setattr(ai, "run_prompt", _fake)
    ws_id, _ = _setup_ws_with_note(logged_in_client)
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/ai/ask",
        json={"question": "what is this?"},
    )
    assert r.status_code == 503


def test_ai_ask_with_anchor_note_includes_body(logged_in_client, monkeypatch):
    captured = {}

    def _fake(prompt, *, model=None, **kw):
        captured["prompt"] = prompt
        return ai.PromptResult(output="answer", exit_code=0, stderr="")

    monkeypatch.setattr(ai, "run_prompt", _fake)
    ws_id, note_id = _setup_ws_with_note(logged_in_client, body_md="特定内容ABC")
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/ai/ask",
        json={"question": "summarize", "note_id": note_id},
    )
    assert r.status_code == 200, r.text
    # Note body must reach the LLM prompt — otherwise "ask about this note" is empty.
    assert "特定内容ABC" in captured["prompt"]


def test_ai_workflow_dispatch_runs_prd(logged_in_client, monkeypatch):
    monkeypatch.setattr(ai, "run_prompt",
                        lambda prompt, **kw: ai.PromptResult(output="case 1\ncase 2", exit_code=0, stderr=""))
    ws_id, note_id = _setup_ws_with_note(logged_in_client, body_md="登录功能 PRD")
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/ai/workflows/prd/test-cases",
        json={"note_id": note_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "prd"
    assert body["action"] == "test-cases"
    assert "case 1" in body["output"]


def test_ai_workflow_404_on_unknown_action(logged_in_client):
    ws_id, note_id = _setup_ws_with_note(logged_in_client)
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/ai/workflows/prd/nonexistent",
        json={"note_id": note_id},
    )
    assert r.status_code == 404


def test_ai_workflow_404_when_note_in_other_workspace(logged_in_client):
    ws_id, _ = _setup_ws_with_note(logged_in_client)
    other_ws, other_note = _setup_ws_with_note(logged_in_client)
    # Use ws_id with other_note's id — must not leak across workspaces.
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/ai/workflows/prd/test-cases",
        json={"note_id": other_note},
    )
    assert r.status_code == 404


def test_ai_workflow_logged_to_audit(logged_in_client, monkeypatch, data_dir):
    from praxnest import audit
    monkeypatch.setattr(ai, "run_prompt",
                        lambda prompt, **kw: ai.PromptResult(output="ok", exit_code=0, stderr=""))
    ws_id, note_id = _setup_ws_with_note(logged_in_client, body_md="content")
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/ai/workflows/prd/test-cases",
        json={"note_id": note_id},
    )
    actions = [r["action"] for r in audit.recent(data_dir)]
    assert any(a.startswith("ai.workflow.prd.test-cases") for a in actions)
