"""Notification — push markdown content to a configured prax notify channel.

V0.2 leaves channel configuration to praxagent (`~/.prax/notify.yaml`)
because rebuilding that schema in praxnest would just duplicate work.
Users config their飞书 / 企业微信 / 个人微信 channels via prax CLI once,
praxnest pushes through them.

Why subprocess instead of import: same reasoning as `ai/client.py` —
praxnest must keep working when praxagent is missing (the push button
just gets a clear 503 instead of a stack trace).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass


class NotifyUnavailable(RuntimeError):
    pass


class PraxNotInstalled(NotifyUnavailable):
    pass


@dataclass
class PushResult:
    ok: bool
    channel: str
    error: str = ""


def list_channels() -> list[str]:
    """Return the names of configured notify channels.

    Reads ``~/.prax/notify.yaml`` directly (it's plain YAML — no need
    to round-trip through prax CLI). Returns empty list if the file
    doesn't exist or has no channels block; never raises.
    """
    from pathlib import Path
    path = Path.home() / ".prax" / "notify.yaml"
    if not path.exists():
        return []
    try:
        import yaml
    except ImportError:
        # praxnest doesn't list pyyaml as a hard dep (yet); if missing,
        # we just can't read the file.
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    channels = data.get("channels") or {}
    return sorted(channels.keys()) if isinstance(channels, dict) else []


def push(*, channel: str, title: str, body: str, timeout: float = 30.0) -> PushResult:
    """Send `title + body` through a prax notify channel.

    Shells out to ``prax wechat send`` for wechat_personal channels and
    a generic ``prax prompt`` fallback for webhook-based channels. We
    use prax's own ``notify.yaml`` resolver so the user's existing
    channel setup just works.

    NOTE: as of praxagent 0.7.x there's no dedicated `prax notify`
    CLI subcommand — we work around by invoking a tiny inline Python
    that imports prax's NotifyTool. If praxagent's API changes here,
    this is the function to update.
    """
    if not channel or not channel.strip():
        raise ValueError("channel must not be empty")
    prax = shutil.which("prax")
    if prax is None:
        raise PraxNotInstalled(
            "推送需要 praxagent。终端跑 `npm install -g praxagent`，"
            "再用 `prax wechat login` 配好渠道。"
        )

    # The simplest cross-channel-type invocation: feed prax a one-shot
    # script that loads notify.yaml + dispatches. Avoids us re-implementing
    # the wechat_work_webhook / feishu_webhook / wechat_personal dispatch.
    script = (
        "import asyncio, sys, json\n"
        "from prax.tools.notify import build_provider\n"
        "from prax.core.config_files import load_notify_config\n"
        "import os\n"
        "cfg = load_notify_config(os.getcwd()).get('channels') or {}\n"
        "ch = cfg.get(sys.argv[1])\n"
        "if not ch:\n"
        "    print(json.dumps({'ok': False, 'error': 'channel not in notify.yaml'}))\n"
        "    sys.exit(1)\n"
        "provider = build_provider(ch)\n"
        "asyncio.run(provider.send(title=sys.argv[2], body=sys.argv[3], level='info'))\n"
        "print(json.dumps({'ok': True}))\n"
    )

    try:
        proc = subprocess.run(
            ["python3", "-c", script, channel, title, body],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        return PushResult(ok=False, channel=channel, error=f"push timed out after {timeout}s")
    except OSError as exc:
        return PushResult(ok=False, channel=channel, error=f"failed to spawn: {exc}")

    if proc.returncode != 0:
        # Try to surface the error from stdout (json) or stderr.
        try:
            payload = json.loads(proc.stdout.strip().split("\n")[-1])
            err = payload.get("error", proc.stderr.strip()[:200])
        except (json.JSONDecodeError, ValueError, IndexError):
            err = (proc.stderr or proc.stdout or "").strip()[:300]
        return PushResult(ok=False, channel=channel, error=err)
    return PushResult(ok=True, channel=channel)
