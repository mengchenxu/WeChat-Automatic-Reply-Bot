"""
上下文构建器 — 三层记忆检索 + 用户档案 + 热梗参考 组装 LLM 上下文。
"""
import logging
import re

from src.user_memory import UserMemoryStore
from src.bot_core import GroupSession
from src.group_memory import GroupMemoryStore
from src.weflow_client import WeFlowMessage

logger = logging.getLogger(__name__)


def build_llm_context(
    msg: WeFlowMessage,
    session: GroupSession,
    user_memory: UserMemoryStore,
    group_memory: GroupMemoryStore,
    bot_nicknames: list,
    search_result: str = "",
) -> str:
    """
    构建注入到 LLM user message 中的完整上下文。
    按优先级组装：群背景 → 相关记忆 → 当前话题 → 参与者 → 热梗 → 最近对话 → 消息内容
    """
    parts = []
    speaker_wxid = msg.sender_name
    speaker_name = msg.display_name or msg.sender_name

    # ---- 1. 群聊背景 ----
    if session.group_context:
        parts.append(f"[群聊背景]\n{session.group_context}")

    # ---- 1.5 群内风格 ----
    style_parts = []
    if session.group_style:
        style_parts.append(f"整体风格: {session.group_style}")
    if session.top_words:
        style_parts.append(f"高频词: {', '.join(session.top_words[:10])}")
    if session.top_emojis:
        style_parts.append(f"常用表情: {' '.join(session.top_emojis[:5])}")
    if style_parts:
        parts.append("[群内风格]\n" + "\n".join(style_parts))

    # ---- 1.6 核心成员风格 ----
    if session.active_users:
        styled_users = []
        for wxid in session.active_users:
            profile = user_memory.get(wxid)
            if profile and profile.speaking_style:
                name = profile.preferred_name or wxid
                styled_users.append(f"  {name}: {profile.speaking_style}")
        if styled_users:
            parts.append("[核心成员风格]\n" + "\n".join(styled_users))

    # ---- 2. 相关情景记忆 ----
    if group_memory and session.topic_keywords:
        try:
            relevant = group_memory.search(session.group_id, session.topic_keywords, limit=3)
            if relevant:
                mem_lines = []
                for mem in relevant:
                    mem_lines.append(f"  · {mem.content}")
                parts.append("[相关记忆]\n" + "\n".join(mem_lines))
        except Exception:
            pass  # 检索失败不阻塞对话

    # ---- 3. 当前话题 ----
    if session.topic_summary:
        parts.append(f"[当前话题]\n{session.topic_summary}")

    # ---- 4. 当前发言者 + 被 @ 的人 ----
    speaker_ctx = user_memory.get_user_context(speaker_wxid)
    if speaker_ctx:
        parts.append(f"当前发言者 — {speaker_ctx}")
    else:
        parts.append(f"当前发言者 — {speaker_name}")

    mentioned_others = [m for m in msg.mentions if m not in bot_nicknames]
    if mentioned_others:
        mentioned_info = []
        for name in mentioned_others:
            profile = user_memory.find_by_name(name)
            if profile and profile.wxid != speaker_wxid:
                ctx = profile.get_context_summary()
                if ctx:
                    mentioned_info.append(f"  @{name} — {ctx}")
                else:
                    mentioned_info.append(f"  @{name}")
            else:
                mentioned_info.append(f"  @{name}")
        if mentioned_info:
            parts.append("消息中 @了:\n" + "\n".join(mentioned_info))

    # ---- 5. 群内其他活跃成员 ----
    if session.active_users:
        other_users = session.active_users - {speaker_wxid}
        if other_users:
            others_ctx = user_memory.get_users_context(list(other_users))
            if others_ctx:
                parts.append(others_ctx)

    # ---- 6. 热梗参考（搜索结果） ----
    if search_result:
        parts.append(f"[热梗参考]\n{search_result}")

    # ---- 7. 最近对话 ----
    if session.history:
        recent = []
        for m in list(session.history)[-6:]:  # 最近 3 轮
            role_label = "用户" if m.role == "user" else "鼠鼠"
            name = getattr(m, 'sender_name', '') or ''
            tag = f"{role_label}({name})" if name else role_label
            recent.append(f"[{tag}]: {m.content[:200]}")
        if recent:
            parts.append("[最近对话]\n" + "\n".join(recent))

    # ---- 8. 当前消息 ----
    parts.append(f"[消息内容]\n{msg.content}")

    return "\n\n".join(parts)


def auto_extract_facts(
    reply: str, speaker_wxid: str, msg_content: str, user_memory: UserMemoryStore
):
    """
    从 LLM 回复中自动提取用户事实。
    不依赖 /remember 指令 —— 检查回复是否包含对用户特征的描述。
    如果发现新事实，自动写入 user_memory。

    这是 "best effort" 的轻量提取，主要事实收集仍靠 LLM 在
    system prompt 指导下主动调用 /remember。
    """
    # 简单模式：查找 "你是..." / "你..." 相关的回应
    patterns = [
        (r'(?:原来|所以)你是[一个位名]?[做搞]?(.+?)[的，。]', "职业"),
        (r'(?:原来|所以)你(?:喜欢|爱)(.+?)[，。]', "喜欢"),
    ]
    for pattern, fact_key in patterns:
        match = re.search(pattern, reply)
        if match and match.group(1).strip():
            value = match.group(1).strip()
            if len(value) <= 20:  # 合理的短事实
                user_memory.merge_fact(speaker_wxid, fact_key, value)
                logger.info("自动提取事实: %s=%s (用户 %s)", fact_key, value, speaker_wxid)


def extract_facts_from_reply(
    reply: str, speaker_wxid: str, user_memory: UserMemoryStore
) -> str:
    """
    从 LLM 回复中提取 /remember 指令并更新用户记忆。
    支持格式:
      /remember @某人 事实: 值
      /remember 事实: 值  （默认记住当前说话者）

    返回清理后的回复文本。
    """
    pattern = r'/remember\s+(?:@(\S+)\s+)?(.+?)\s*:\s*(.+)'
    facts: list[tuple[str, str, str]] = []

    def _process(m: re.Match) -> str:
        at_name = (m.group(1) or "").strip()
        key = m.group(2).strip()
        value = m.group(3).strip()
        if key and value:
            facts.append((at_name, key, value))
        return ""

    clean_reply = re.sub(pattern, _process, reply)

    for at_name, key, value in facts:
        if at_name:
            target = user_memory.find_by_name(at_name)
            if target:
                user_memory.merge_fact(target.wxid, key, value)
                logger.info("LLM 记住了 @%s: %s = %s", at_name, key, value)
            else:
                logger.debug("未找到用户 @%s，跳过记忆", at_name)
        else:
            user_memory.merge_fact(speaker_wxid, key, value)
            logger.info("LLM 记住了当前用户: %s = %s", key, value)

    clean_reply = re.sub(r'\n{3,}', '\n\n', clean_reply).strip()
    return clean_reply


def extract_context_from_reply(reply: str) -> tuple[str, str | None]:
    """
    从 LLM 回复中提取 /context 群背景更新指令。
    返回 (清理后的回复, 群背景文本或None)。
    """
    pattern = r'/context\s+(.+?)(?:\n|$)'
    context_text: str | None = None

    def _process(m: re.Match) -> str:
        nonlocal context_text
        context_text = m.group(1).strip()
        return ""

    clean_reply = re.sub(pattern, _process, reply)
    clean_reply = re.sub(r'\n{3,}', '\n\n', clean_reply).strip()
    return clean_reply, context_text
