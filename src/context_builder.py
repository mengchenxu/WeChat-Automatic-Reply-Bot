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
            if profile is None:
                # 找不到 → 新建并立即保存（首次被 @的群成员）
                user_memory.get_or_create(name, name)
                user_memory.save()
                profile = user_memory.get(name)
                logger.info("发现新群成员: %s", name)
            if profile and profile.wxid != speaker_wxid:
                display = profile.preferred_name or name
                ctx = profile.get_context_summary()
                if ctx:
                    mentioned_info.append(f"  @{display} — {ctx}")
                else:
                    mentioned_info.append(f"  @{display}")
            elif profile and profile.wxid == speaker_wxid:
                pass  # 发言人 @ 自己，跳过
            else:
                mentioned_info.append(f"  @{name}")
        if mentioned_info:
            parts.append("消息中 @了:\n" + "\n".join(mentioned_info))

    # ---- 4.5 实体解析：扫描消息正文中提到的已知群成员 ----
    entities = []
    mentionable_names = []
    # 使用所有已知用户（而非 active_users），确保重启后也能识别
    all_wxids = user_memory.get_all_wxids()
    if all_wxids:
        entities, mentionable_names = _resolve_entities_in_message(
            msg.content, user_memory, all_wxids,
            group_memory, msg.roomid
        )
        # 过滤掉已在显式 @了 列表里的人（避免重复）
        mentioned_set = {m.lower() for m in mentioned_others}
        new_entities = [e for e in entities if e["name"].lower() not in mentioned_set]
        if entities:
            logger.info("实体解析: 在消息中扫到 %d 个已知成员: %s",
                        len(entities),
                        [(e["name"], e["profile"].preferred_name or e["name"]) for e in entities])
        if new_entities:
            entity_lines = []
            for e in new_entities:
                name = e["profile"].preferred_name or e["name"]
                ctx = e["profile"].get_context_summary()
                line = f"  @{name}"
                if ctx:
                    line += f" — {ctx}"
                entity_lines.append(line)
                # 附上相关群记忆（让 LLM 知道"这人是干嘛的"）
                if e.get("memories"):
                    for mem in e["memories"]:
                        entity_lines.append(f"    相关: {mem.content}")
            if entity_lines:
                parts.append("[消息中可能提到的人]\n" + "\n".join(entity_lines))
                logger.info("实体解析注入上下文: %d 人（去重后）", len(new_entities))

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

    # ---- 8. 动态格式指令（近因指令，确保 LLM 用 @名字） ----
    # 当前发言人由 at_sender 自动 @，LLM 不需要在开头再写一次
    all_mentionable = [m for m in mentioned_others if m.lower() != speaker_name.lower()]
    for e in entities:
        name = e["profile"].preferred_name or e["name"]
        if name.lower() != speaker_name.lower() and name not in all_mentionable:
            all_mentionable.append(name)
    if all_mentionable:
        parts.append(
            f"[重要：回复格式]\n"
            f"回复对象 @{speaker_name} 已由系统自动处理，你无需在回复开头写 @{speaker_name}。\n"
            f"如果你在正文中提到以下其他群成员，在提到的地方用 @名字 格式（内联 mention）：{', '.join(all_mentionable)}\n"
            f"示例: \"宁这转进如风啊😅 刚才还香炉了，现在又变成想她了，宁这抽象程度已经超越 @贯一 的cos马好吧💧\""
        )

    # ---- 9. 当前消息 ----
    parts.append(f"[消息内容]\n{msg.content}")

    return "\n\n".join(parts)


# ----------------------------------------------------------------
# 实体解析：扫描消息正文中提到的已知群成员
# ----------------------------------------------------------------
def _resolve_entities_in_message(
    content: str,
    user_memory,
    active_users: set,
    group_memory,
    room_id: str,
) -> tuple:
    """
    扫描消息正文，找到可能被自然提及的已知群成员。
    不依赖微信 @ 标记 — 纯文本"子南那个事"也会被识别。

    返回:
      entities: [{"name": "子南", "profile": UserProfile, "memories": [...]}, ...]
      mentionable_names: ["子南", "贯一", ...]
    """
    if not content or not active_users:
        return [], []

    # 收集所有候选名字，按长度降序排列（最长匹配优先，避免"子"误匹配"子南"）
    candidates: list[tuple[int, str, str, object]] = []  # (len, name, wxid, profile)
    for wxid in active_users:
        profile = user_memory.get(wxid)
        if not profile:
            continue
        names = set()
        if profile.preferred_name:
            names.add(profile.preferred_name)
        for dn in profile.display_names:
            if dn:
                names.add(dn)
        for alias in profile.aliases:
            if alias:
                names.add(alias)
        for name in names:
            if name and len(name) >= 2:
                candidates.append((len(name), name, wxid, profile))

    # 去重（同名只保留一份）+ 按长度降序
    candidates.sort(key=lambda x: x[0], reverse=True)
    seen_names = set()
    unique_candidates = []
    for length, name, wxid, profile in candidates:
        nlower = name.lower()
        if nlower not in seen_names:
            seen_names.add(nlower)
            unique_candidates.append((length, name, wxid, profile))

    # 在消息中搜索，避免重叠匹配
    matched_positions: set = set()
    entities_by_wxid: dict = {}

    content_lower = content.lower()
    for _, name, wxid, profile in unique_candidates:
        name_lower = name.lower()
        search_start = 0
        while True:
            idx = content_lower.find(name_lower, search_start)
            if idx == -1:
                break

            # 重叠检查：该位置是否已被更长的名字占用
            positions = set(range(idx, idx + len(name)))
            if positions & matched_positions:
                search_start = idx + 1
                continue

            # 拉丁名词边界检查
            if _is_latin_name(name):
                if not _latin_word_boundary(content, idx, len(name)):
                    search_start = idx + 1
                    continue

            # 匹配成功
            matched_positions |= positions
            if wxid not in entities_by_wxid:
                entities_by_wxid[wxid] = {"name": name, "profile": profile}
            break

    # 检索每个匹配人的相关群记忆
    entities = []
    mentionable_names = []
    for wxid, info in entities_by_wxid.items():
        entity = {"name": info["name"], "profile": info["profile"], "memories": []}
        if group_memory and room_id:
            keywords = [info["name"]]
            pname = info["profile"].preferred_name
            if pname and pname != info["name"]:
                keywords.append(pname)
            try:
                entity["memories"] = group_memory.search(room_id, keywords, limit=2)
            except Exception:
                pass
        entities.append(entity)
        mentionable_names.append(info["profile"].preferred_name or info["name"])

    return entities, mentionable_names


def _is_latin_name(name: str) -> bool:
    """名字是否包含拉丁字母（如 B L U E）"""
    return any(c.isascii() and c.isalpha() for c in name)


def _latin_word_boundary(content: str, idx: int, length: int) -> bool:
    """检查拉丁名在 content[idx:idx+length] 是否有词边界。"""
    if idx > 0:
        prev = content[idx - 1]
        if prev.isascii() and (prev.isalpha() or prev.isdigit()):
            return False
    end = idx + length
    if end < len(content):
        nxt = content[end]
        if nxt.isascii() and (nxt.isalpha() or nxt.isdigit()):
            return False
    return True


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
            if len(value) <= 20:
                user_memory.merge_fact(speaker_wxid, fact_key, value)
                logger.info("自动提取事实: %s=%s (用户 %s)", fact_key, value, speaker_wxid)

    # 外号检测：从用户消息和 LLM 回复中提取外号
    # 模式: "叫我xxx"、"大家都叫我xxx"、"{name}就是{target}" 等
    alias_patterns = [
        r'(?:叫我|喊我|大家(?:都)?叫我)\s*["「]?(.+?)["」]?(?:[，。！\s]|$)',
        r'["「](.+?)["」]\s*(?:就是|是)\s*(?:我|本人)',
    ]
    for pat in alias_patterns:
        for m in re.finditer(pat, msg_content):
            alias = m.group(1).strip()
            if 1 <= len(alias) <= 10 and alias != "我":
                user_memory.add_alias(speaker_wxid, alias)
                logger.info("自动提取外号: %s → %s", speaker_wxid, alias)


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
        # 特殊处理：外号/别名 存到 aliases
        if key.strip() in ("外号", "别名", "绰号"):
            if at_name:
                target = user_memory.find_by_name(at_name)
                if target:
                    user_memory.add_alias(target.wxid, value)
                    logger.info("LLM 记住了 @%s 的外号: %s", at_name, value)
                else:
                    logger.debug("未找到用户 @%s，跳过外号", at_name)
            else:
                user_memory.add_alias(speaker_wxid, value)
                logger.info("LLM 记住了当前用户的外号: %s", value)
        elif at_name:
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
