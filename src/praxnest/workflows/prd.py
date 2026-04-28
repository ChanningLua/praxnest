"""PRD assistant — generate test cases / extract requirements / acceptance checklist
from the current note's body.

Each action is a one-shot LLM call: we DON'T let the LLM use Bash or
edit files. The output is markdown the user can copy-paste back into
their PRD or into a sibling note.
"""

from __future__ import annotations

from typing import Any

from .. import ai


def _ask(*, note: dict[str, Any], system: str, instruction: str, model: str) -> ai.PromptResult:
    """Compose system + instruction + note body, run prompt once."""
    body = (note.get("body_md") or "").strip()
    if not body:
        raise ai.AIUnavailable("笔记正文为空，AI 没东西可分析。先在笔记里写下需求。")
    prompt = (
        f"# 你是一名资深产品经理 + 测试工程师。\n"
        f"{system}\n\n"
        f"# 任务\n{instruction}\n\n"
        f"# 输入：当前 PRD 文档《{note.get('title', '')}》\n"
        f"---\n{body}\n---\n\n"
        f"# 输出要求\n直接返回 markdown 列表，不要寒暄不要总结，让用户能直接复制到他的笔记里。"
    )
    return ai.run_prompt(prompt, model=model)


def gen_test_cases(*, note: dict[str, Any], model: str) -> ai.PromptResult:
    return _ask(
        note=note, model=model,
        system="目标是把 PRD 里的功能点穷举成可执行的测试用例。",
        instruction=(
            "基于上面 PRD 内容，生成测试用例清单。每条用例包含："
            "**用例名 / 前置条件 / 操作步骤 / 期望结果**。"
            "覆盖正常路径 + 至少 3 类异常路径（输入边界、并发冲突、权限越界）。"
        ),
    )


def extract_requirements(*, note: dict[str, Any], model: str) -> ai.PromptResult:
    return _ask(
        note=note, model=model,
        system="目标是从 PRD 描述里抽取出原子需求，便于工程估时。",
        instruction=(
            "把上面 PRD 拆成原子需求列表。每条需求包含："
            "**需求 ID（R-001 起编号） / 一句话描述 / 涉及模块 / 估算复杂度 (S/M/L)**。"
        ),
    )


def acceptance_checklist(*, note: dict[str, Any], model: str) -> ai.PromptResult:
    return _ask(
        note=note, model=model,
        system="目标是给产研测三方一个共同认可的『验收完成』清单。",
        instruction=(
            "基于上面 PRD，输出一份验收清单（checkbox 形式）。"
            "覆盖：功能验收 / 性能验收 / 安全验收 / 兼容性验收 / 文档验收。"
            "每条清单项要可勾选、可验证（避免『系统稳定运行』这种不可量化的描述）。"
        ),
    )


WORKFLOW = {
    "kind": "prd",
    "label": "PRD 助手",
    "actions": {
        "test-cases": gen_test_cases,
        "requirements": extract_requirements,
        "checklist": acceptance_checklist,
    },
}
