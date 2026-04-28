"""Built-in AI workflows: PRD / Bug / Test report.

Each workflow is a dict::

    {
        "kind": "prd",
        "label": "PRD 助手",
        "actions": {
            "test-cases": <callable(note, model) → PromptResult>,
            ...
        },
    }

The route layer (`routes/ai.py`) discovers actions by name and forwards
the call. Adding a workflow = drop a new module here that exports
``WORKFLOW`` + register it in ``WORKFLOWS``.

Why this dispatcher pattern instead of plain endpoints per action: it
keeps the URL shape uniform (``/ai/workflows/{kind}/{action}``), so
the GUI can list / button-ify all workflows from one ``/status`` call
without hard-coding routes per kind.
"""

from __future__ import annotations

from .bug import WORKFLOW as _BUG_WORKFLOW
from .prd import WORKFLOW as _PRD_WORKFLOW
from .test_report import WORKFLOW as _TEST_REPORT_WORKFLOW


WORKFLOWS = [_PRD_WORKFLOW, _BUG_WORKFLOW, _TEST_REPORT_WORKFLOW]


def get_workflow(kind: str) -> dict | None:
    for w in WORKFLOWS:
        if w["kind"] == kind:
            return w
    return None


__all__ = ["WORKFLOWS", "get_workflow"]
