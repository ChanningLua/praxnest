"""AI integration — thin shell-out to praxagent's `prax prompt`.

Why subprocess instead of importing prax: praxagent is npm-distributed
(not on PyPI), and we want praxnest to keep working when praxagent is
missing (the AI features just disable themselves with a clear error).
A subprocess boundary also matches praxagent's permission model: each
`prax prompt` invocation gets its own isolated session, so a runaway
AI in one workflow can't bleed into another.
"""

from .client import (
    AIUnavailable,
    PraxNotInstalled,
    PromptResult,
    prax_available,
    run_prompt,
)

__all__ = [
    "AIUnavailable",
    "PraxNotInstalled",
    "PromptResult",
    "prax_available",
    "run_prompt",
]
