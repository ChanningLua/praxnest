"""Notify integration — push to prax channels via subprocess.

Real prax never runs in these tests. We mock subprocess.run so failures
here are about our wrapper / route logic, not whether prax / yaml /
notify channels exist on the test machine.
"""

from __future__ import annotations

import subprocess as _subprocess
from pathlib import Path

import pytest

from praxnest import notify


# ── notify.list_channels (reads ~/.prax/notify.yaml) ────────────────────────


def test_list_channels_empty_when_yaml_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    assert notify.list_channels() == []


def test_list_channels_reads_yaml_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    cfg = tmp_path / "fake-home" / ".prax" / "notify.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "channels:\n"
        "  daily-digest:\n"
        "    provider: feishu_webhook\n"
        "    url: https://x\n"
        "  team-wechat:\n"
        "    provider: wechat_personal\n"
        "    account_id: abc\n",
        encoding="utf-8",
    )
    chans = notify.list_channels()
    assert chans == ["daily-digest", "team-wechat"]   # sorted


def test_list_channels_handles_garbage_yaml(tmp_path, monkeypatch):
    """Bad YAML must NOT crash the GUI's channel-list dropdown."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    cfg = tmp_path / "fake-home" / ".prax" / "notify.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("not: valid: yaml: [", encoding="utf-8")
    assert notify.list_channels() == []


# ── notify.push (subprocess wrapper) ────────────────────────────────────────


def test_push_raises_if_prax_missing(monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    with pytest.raises(notify.PraxNotInstalled):
        notify.push(channel="x", title="t", body="b")


def test_push_rejects_empty_channel(monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/fake/prax")
    with pytest.raises(ValueError, match="channel"):
        notify.push(channel="  ", title="t", body="b")


def test_push_returns_ok_on_success(monkeypatch):
    class _FakeProc:
        returncode = 0; stdout = '{"ok": true}\n'; stderr = ""

    monkeypatch.setattr(notify.shutil, "which", lambda _: "/fake/prax")
    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _FakeProc())
    result = notify.push(channel="daily-digest", title="t", body="b")
    assert result.ok is True
    assert result.channel == "daily-digest"


def test_push_surfaces_error_from_prax(monkeypatch):
    """Non-zero exit → ok=False with a useful error."""
    class _FakeProc:
        returncode = 1
        stdout = '{"ok": false, "error": "channel not in notify.yaml"}'
        stderr = ""

    monkeypatch.setattr(notify.shutil, "which", lambda _: "/fake/prax")
    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _FakeProc())
    result = notify.push(channel="bogus", title="t", body="b")
    assert result.ok is False
    assert "channel not in" in result.error


def test_push_handles_timeout(monkeypatch):
    monkeypatch.setattr(notify.shutil, "which", lambda _: "/fake/prax")
    def _to(*a, **kw): raise _subprocess.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(_subprocess, "run", _to)
    result = notify.push(channel="x", title="t", body="b", timeout=1)
    assert result.ok is False
    assert "timed out" in result.error


# ── HTTP layer ─────────────────────────────────────────────────────────────


def test_http_list_channels_reads_yaml(logged_in_client, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fake-home")
    cfg = tmp_path / "fake-home" / ".prax" / "notify.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("channels:\n  alpha: {provider: feishu_webhook, url: x}\n", encoding="utf-8")

    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.get(f"/api/workspaces/{ws_id}/notify/channels")
    assert r.json()["channels"] == ["alpha"]


def test_http_push_with_note_id(logged_in_client, monkeypatch):
    monkeypatch.setattr(notify, "push",
                        lambda **kw: notify.PushResult(ok=True, channel=kw["channel"]))
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    note = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notes",
        json={"title": "Report", "body_md": "## summary\n..."},
    ).json()
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notify/push",
        json={"channel": "wechat", "note_id": note["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_http_push_with_freeform_body(logged_in_client, monkeypatch):
    captured = {}
    def _fake(**kw):
        captured.update(kw)
        return notify.PushResult(ok=True, channel=kw["channel"])
    monkeypatch.setattr(notify, "push", _fake)

    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notify/push",
        json={"channel": "wechat", "title": "AI 输出", "body": "hello world"},
    )
    assert r.status_code == 200
    assert captured["title"] == "AI 输出"
    assert captured["body"] == "hello world"


def test_http_push_returns_502_on_upstream_failure(logged_in_client, monkeypatch):
    monkeypatch.setattr(notify, "push",
                        lambda **kw: notify.PushResult(ok=False, channel=kw["channel"], error="iLink ret=-2"))
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notify/push",
        json={"channel": "wechat", "body": "x"},
    )
    assert r.status_code == 502
    assert "iLink ret=-2" in r.json()["detail"]


def test_http_push_returns_503_when_prax_missing(logged_in_client, monkeypatch):
    def _missing(**kw): raise notify.PraxNotInstalled("install praxagent")
    monkeypatch.setattr(notify, "push", _missing)
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notify/push",
        json={"channel": "wechat", "body": "x"},
    )
    assert r.status_code == 503


def test_http_push_400_when_nothing_to_push(logged_in_client):
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    r = logged_in_client.post(
        f"/api/workspaces/{ws_id}/notify/push",
        json={"channel": "wechat"},  # no note_id, no body
    )
    assert r.status_code == 400
    assert "nothing to push" in r.json()["detail"]


def test_http_push_logged_to_audit(logged_in_client, monkeypatch, data_dir):
    from praxnest import audit
    monkeypatch.setattr(notify, "push",
                        lambda **kw: notify.PushResult(ok=True, channel=kw["channel"]))
    ws_id = logged_in_client.post("/api/workspaces", json={"name": "ws"}).json()["id"]
    logged_in_client.post(
        f"/api/workspaces/{ws_id}/notify/push",
        json={"channel": "wechat", "body": "x"},
    )
    actions = [r["action"] for r in audit.recent(data_dir)]
    assert "notify.push" in actions
