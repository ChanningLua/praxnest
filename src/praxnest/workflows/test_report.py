"""测试报告 workflow — 把测试结果（json / markdown 表格 / 散文描述）
压成 一份易读的执行摘要 + 趋势对比，便于推送给 PM / 研发负责人。
"""

from __future__ import annotations

from typing import Any

from .. import ai


_SYSTEM = (
    "你是一名 QA Lead。任务是把原始测试输出（json / 表格 / 文字）"
    "压成给非 QA 看得懂的执行摘要。"
)


def summary(*, note: dict[str, Any], model: str) -> ai.PromptResult:
    """Generate a digest from raw test data in the note body.
    Output is suitable for pasting into wechat / feishu groups."""
    body = (note.get("body_md") or "").strip()
    if not body:
        raise ai.AIUnavailable("没有测试数据。把测试 json / 报告内容粘进笔记后再点。")

    prompt = (
        f"# 角色\n{_SYSTEM}\n\n"
        f"# 输入：测试报告原始数据《{note.get('title', '')}》\n"
        f"---\n{body}\n---\n\n"
        f"# 任务\n"
        f"输出一份 markdown 摘要，**适合直接发到企业微信 / 飞书群**：\n\n"
        f"## 📊 总览\n"
        f"- 通过率: X / Y (Z%)\n"
        f"- 新增失败: 最严重的 1-3 条简述\n"
        f"- 持续失败: 哪些是老问题（>3 天没修）\n\n"
        f"## 🔴 必须修\n"
        f"列 P0 / P1 失败用例，每条一行，附带：用例名 + 一句话原因\n\n"
        f"## 🟡 关注\n"
        f"P2 失败 / 不稳定（间歇通过）的用例\n\n"
        f"## 📈 趋势\n"
        f"如果原始数据里能看出今天 vs 昨天 / 上周对比，给一句话评论\n\n"
        f"## ✍ 建议\n"
        f"给研发 / PM 看的下一步动作（不超过 3 条）\n\n"
        f"原则：**只基于上面给的数据写**，不要编造。如果某个 section 没数据，写「数据不足」。"
    )
    return ai.run_prompt(prompt, model=model)


WORKFLOW = {
    "kind": "test-report",
    "label": "测试报告",
    "actions": {
        "summary": summary,
    },
}
