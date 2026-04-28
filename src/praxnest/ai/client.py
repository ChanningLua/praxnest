"""``prax prompt`` subprocess wrapper.

Single source of truth for "ask the LLM about X". Workflows
(`workflows/prd.py` etc.) compose a `system_prompt + user_prompt`
and call `run_prompt(...)`; we don't expose the prax CLI's full
surface, just what praxnest actually needs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


class AIUnavailable(RuntimeError):
    """Generic 'AI didn't work this time' error. Workflows wrap this
    into a friendlier UI message; routes return 502 / 503."""


class PraxNotInstalled(AIUnavailable):
    """`prax` not on PATH. The most common AIUnavailable subtype, so
    callers can show a specific install hint."""


@dataclass
class PromptResult:
    output: str           # the LLM's text response
    exit_code: int
    stderr: str           # captured for debugging; not shown by default


def prax_available() -> bool:
    """Cheap detector — does `prax` resolve on PATH?
    Used by the GUI to decide whether to show AI buttons or a "install
    praxagent first" banner."""
    return shutil.which("prax") is not None


def run_prompt(
    prompt: str,
    *,
    model: str | None = None,
    timeout: float = 90.0,
    extra_env: dict[str, str] | None = None,
) -> PromptResult:
    """Run `prax prompt <prompt>` once and return the captured output.

    `model`: pin a specific model (e.g. ``gpt-5.4``) to avoid
    tier-routing surprises. Recommended for workflows; if None we let
    prax pick.

    `timeout`: seconds. Workflows are expected to be one-shot LLM calls,
    not long-running agent loops, so 90s is generous.
    """
    prax = shutil.which("prax")
    if prax is None:
        raise PraxNotInstalled(
            "praxnest's AI features need praxagent. Install with `npm install -g praxagent`."
        )

    if not prompt or not prompt.strip():
        raise ValueError("prompt must not be empty")

    argv = [prax, "prompt", prompt]
    if model:
        argv += ["--model", model]
    # Workflows are short, deterministic LLM calls — no Bash, no tools.
    # Run in read-only mode so the LLM can't surprise us by editing
    # files in the workspace.
    argv += ["--permission-mode", "read-only"]

    env = {**os.environ, **(extra_env or {})}
    try:
        proc = subprocess.run(
            argv,
            capture_output=True, text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise AIUnavailable(f"prax prompt timed out after {timeout}s")
    except OSError as exc:
        raise AIUnavailable(f"prax prompt failed to start: {exc}") from exc

    # Both stdout (LLM text) and stderr (prax internal logs in gray) come back.
    # We strip the [prax] gray-prefixed lines from stdout — they're
    # not part of the answer, they're status output.
    output = _strip_prax_stderr_lines(proc.stdout or "").strip()

    return PromptResult(
        output=output,
        exit_code=proc.returncode,
        stderr=(proc.stderr or "").strip(),
    )


_PRAX_LOG_PREFIX = "[prax]"


def _strip_prax_stderr_lines(text: str) -> str:
    """Drop `[prax] ...` status lines that prax emits inline.

    These appear because prax's CLI mixes structured status into stdout
    when it can't tell stdout from stderr (terminal vs subprocess). We
    only want the LLM's actual response in the output we hand to the
    workflow.
    """
    if not text:
        return text
    out_lines: list[str] = []
    for raw in text.splitlines():
        # Strip ANSI color codes prax sometimes emits even when not on a tty.
        stripped = _strip_ansi(raw).strip()
        if stripped.startswith(_PRAX_LOG_PREFIX):
            continue
        out_lines.append(raw)
    return "\n".join(out_lines)


def _strip_ansi(s: str) -> str:
    """Remove ANSI escape sequences (CSI). Just enough to detect the
    `[prax]` prefix; we don't actually rewrite the displayed output."""
    import re
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", s)
