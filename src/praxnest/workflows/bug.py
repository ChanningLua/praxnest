"""Bug 协同 — bug 报告模板填充 + 严重度 / 关联代码 / 推荐指派人。

Workflow 的核心价值：研发不用每次手撕 bug 报告，QA 写完粗描述，
AI 自动补全严重度评估、可能涉及的代码片段路径、基于历史指派记录的
推荐 owner。
"""

from __future__ import annotations

from typing import Any

from .. import ai


_SYSTEM = (
    "你是一个资深 SRE + tech lead。"
    "用户给你一份初步的 bug 描述（可能很糙），你的任务是补全成结构化的 bug 报告。"
)


def assess(*, note: dict[str, Any], model: str) -> ai.PromptResult:
    """One-shot evaluator. Output: severity / suggested assignee /
    related-code hints / reproduction quality assessment."""
    body = (note.get("body_md") or "").strip()
    if not body:
        raise ai.AIUnavailable("bug 描述为空。先把现象、复现步骤写下来再让 AI 评估。")

    prompt = (
        f"# 角色\n{_SYSTEM}\n\n"
        f"# 输入：bug 报告《{note.get('title', '')}》\n"
        f"---\n{body}\n---\n\n"
        f"# 任务\n"
        f"分析以上 bug 描述，输出 markdown 格式：\n\n"
        f"## 严重度评估\n"
        f"- 等级：[P0 / P1 / P2 / P3] —— 引用具体定义\n"
        f"- 理由：一两句话\n\n"
        f"## 复现质量\n"
        f"- 当前描述能不能让别人复现？缺哪些信息？\n\n"
        f"## 推荐排查方向\n"
        f"- 列 3-5 个可能的代码模块 / 配置文件 / 日志位置\n\n"
        f"## 推荐指派给\n"
        f"- 基于现象推断该工种（前端/后端/SRE/DBA/...），不要瞎猜具体人名\n\n"
        f"严格按上面四节输出，不要寒暄不要总结。"
    )
    return ai.run_prompt(prompt, model=model)


def fill_template(*, note: dict[str, Any], model: str) -> ai.PromptResult:
    """Take the user's rough description, return a polished bug
    report skeleton (replace the body wholesale)."""
    body = (note.get("body_md") or "").strip()
    if not body:
        raise ai.AIUnavailable("先在笔记里写下现象，再让 AI 套模板。")

    prompt = (
        f"# 角色\n{_SYSTEM}\n\n"
        f"# 输入：粗略 bug 描述\n"
        f"---\n{body}\n---\n\n"
        f"# 任务\n"
        f"把上面的描述改写成下面的标准模板（缺失的字段用 `[待填]` 占位，不要瞎编）：\n\n"
        f"## 现象\n[一两句话描述用户看到了什么]\n\n"
        f"## 影响范围\n[受影响的功能 / 用户群 / 频率]\n\n"
        f"## 复现步骤\n1. ...\n2. ...\n3. ...\n\n"
        f"## 期望结果\n[应该是什么]\n\n"
        f"## 实际结果\n[实际看到什么]\n\n"
        f"## 环境信息\n- OS: [待填]\n- 版本: [待填]\n- 浏览器: [待填]\n\n"
        f"## 已尝试的排查\n[如果原描述里提到了]\n\n"
        f"严格按上面模板输出 markdown，不要解释你做了什么。"
    )
    return ai.run_prompt(prompt, model=model)


WORKFLOW = {
    "kind": "bug",
    "label": "Bug 协同",
    "actions": {
        "assess": assess,
        "fill-template": fill_template,
    },
}
