"""Prompt 组装阶段 — 四段式 system + 摘要 + 历史 + 当前消息"""
from typing import Dict, List

from src.enrich import EnrichedCtx


def build_prompt(enriched: EnrichedCtx, system_prompt: str) -> List[Dict[str, str]]:
    """构建 LLM 消息列表。返回 [{"role": "system", "content": ...},
    {"role": "user", "content": ...}]。"""

    messages = [{"role": "system", "content": system_prompt}]

    # 用户消息：四段组装
    sections = []

    # 1. 群聊摘要
    if enriched.group_summary:
        sections.append(f"[群聊背景] {enriched.group_summary}")

    # 2. 群聊记录
    if enriched.history:
        lines = []
        for m in enriched.history:
            name = m.sender_name or "未知"
            role_label = "" if m.role == "assistant" else f"{name}: "
            lines.append(f"{role_label}{m.content[:200]}")
        sections.append("[群聊记录]\n" + "\n".join(lines))

    # 3. 当前消息
    sender = enriched.parsed.sender_name
    sections.append(f"[当前消息]\n@{sender}: {enriched.parsed.content}")

    # 4. 格式提醒（仅当有可 mention 的人时）
    if enriched.mentionable_names:
        names = ", ".join(enriched.mentionable_names[:8])
        sections.append(
            f"[格式] 回复对象 @{sender} 系统自动处理。"
            f"正文提到其他人用 @名字: {names}"
        )

    user_content = "\n\n".join(sections)
    messages.append({"role": "user", "content": user_content})

    return messages
