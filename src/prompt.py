"""Prompt 组装阶段 — 四段式 system + 摘要 + 历史 + 当前消息"""
from src.enrich import EnrichedCtx

_EXTRACTION_SYSTEM = """你是一个群聊记录分析助手。阅读群聊消息，提取值得记住的内容。

判断标准：
- 群成员的明确表态、偏好、计划（如"我下周去日本"）
- 群的共识或决定（如"大家都觉得去公园比较好"）
- 有趣的群内梗/笑点
- 重要的话题转折
- 群友之间的关系（同事、同学、朋友、CP 等）

只提取确定的事实和关系，不要推测。没有值得记的就返回空数组。"""


def build_extraction_prompt(recent_messages: list) -> list[dict[str, str]]:
    """构建记忆提取 prompt。recent_messages 是 ChatMsg 列表。
    返回 [{"role": "system", ...}, {"role": "user", ...}]。"""
    lines = []
    for m in recent_messages[-15:]:
        name = getattr(m, 'sender_name', '') or '未知'
        role = "用户" if m.role == "user" else "助手"
        lines.append(f"[{role}({name})]: {m.content[:150]}")

    user = f"""最新消息:
{chr(10).join(lines)}

对每条值得记住的内容，返回一个 JSON 数组（不要 markdown 代码块，只返回纯 JSON 数组）：
[
  {{
    "content": "记忆内容",
    "category": "event|decision|fact|joke|topic_change",
    "keywords": ["k1", "k2"],
    "participants": ["名字1"],
    "importance": 1-5,
    "facts": [
      {{"person": "名字", "key": "事实名", "value": "事实值"}}
    ],
    "relations": [
      {{"person": "名字", "target": "名字", "label": "同事|同学|朋友|CP|亲戚"}}
    ]
  }}
]

最多 3 条记忆，每条最多 2 个 facts，relations 可选。没有则返回 []"""

    return [
        {"role": "system", "content": _EXTRACTION_SYSTEM},
        {"role": "user", "content": user},
    ]


def build_prompt(enriched: EnrichedCtx, system_prompt: str) -> list[dict[str, str]]:
    """构建 LLM 消息列表。返回 [{"role": "system", "content": ...},
    {"role": "user", "content": ...}]。"""

    messages = [{"role": "system", "content": system_prompt}]

    # 用户消息：四段组装
    sections = []

    sender = enriched.parsed.sender_name or "未知"

    # 1. 群聊摘要
    if enriched.group_summary:
        sections.append(f"[群聊摘要] {enriched.group_summary}")

    # 1.5 群内风格
    if enriched.group_style:
        sections.append(f"[群内风格] {enriched.group_style}")

    # 2. 群聊记录（最多 10 条）
    if enriched.history:
        lines = []
        for m in enriched.history[-10:]:
            name = m.sender_name or "未知"
            role_label = "" if m.role == "assistant" else f"{name}: "
            lines.append(f"{role_label}{m.content[:200]}")
        sections.append("[群聊记录]\n" + "\n".join(lines))

    # 3. 当前消息
    sections.append(f"[当前消息]\n@{sender}: {enriched.parsed.content}")

    user_content = "\n\n".join(sections)
    messages.append({"role": "user", "content": user_content})

    return messages
