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
    构建注入到 LLM user message 中的精简上下文。
    优先级：最近对话 > 当前消息 > 在场的人 > 群聊摘要 > 格式提醒
    """
    parts = []
    speaker_wxid = msg.sender_name
    speaker_name = msg.display_name or msg.sender_name

    # ================================================================
    # 1. 最近对话 — LLM 看到的第一样东西，像真人一样先看聊天记录
    # ================================================================
    if session.history:
        recent = []
        for m in list(session.history)[-16:]:  # 最近 8 轮
            role_label = "用户" if m.role == "user" else "鼠鼠"
            name = getattr(m, 'sender_name', '') or ''
            tag = f"{role_label}({name})" if name else role_label
            recent.append(f"[{tag}]: {m.content[:200]}")
        if recent:
            parts.append("[最近对话]\n" + "\n".join(recent))

    # ================================================================
    # 2. 当前消息 — 紧接对话之后
    # ================================================================
    parts.append(f"[消息]\n@{speaker_name}: {msg.content}")

    # ================================================================
    # 3. 在场的人 — 合并实体解析 + 显式 @ + 群成员，每人一行极简格式
    # ================================================================
    mentioned_others = [m for m in msg.mentions if m not in bot_nicknames]
    mentioned_set = {m.lower() for m in mentioned_others}

    # 显式 @ 的人
    people: dict[str, dict] = {}  # wxid_or_name -> {name, facts_str}
    for name in mentioned_others:
        profile = user_memory.find_by_name(name)
        if profile is None:
            user_memory.get_or_create(name, name)
            user_memory.save()
            profile = user_memory.get(name)
            logger.info("发现新群成员: %s", name)
        if profile and profile.wxid != speaker_wxid:
            people[profile.wxid] = {
                "name": profile.preferred_name or name,
                "facts_str": _brief_facts(profile),
            }

    # 实体解析扫到的人
    entities = []
    all_wxids = user_memory.get_all_wxids()
    if all_wxids:
        entities, _ = _resolve_entities_in_message(
            msg.content, user_memory, all_wxids,
            group_memory, msg.roomid
        )
        if entities:
            logger.info("实体解析: %d 人 — %s",
                        len(entities),
                        [(e["name"], e["profile"].preferred_name or e["name"]) for e in entities])
        for e in entities:
            wxid = e["profile"].wxid
            if wxid not in people and e["name"].lower() not in mentioned_set and wxid != speaker_wxid:
                people[wxid] = {
                    "name": e["profile"].preferred_name or e["name"],
                    "facts_str": _brief_facts(e["profile"]),
                    "memories": e.get("memories", []),
                }

    if people:
        people_lines = []
        for wxid, info in people.items():
            line = f"  @{info['name']}"
            if info["facts_str"]:
                line += f" — {info['facts_str']}"
            people_lines.append(line)
            # 相关记忆（极简，最多 1 条）
            for mem in (info.get("memories") or [])[:1]:
                people_lines.append(f"    ∟ {mem.content}")
        parts.append("[在场的人]\n" + "\n".join(people_lines))

    # ================================================================
    # 4. 群聊摘要 — 合并 群背景 + 当前话题 + 相关记忆
    # ================================================================
    summary_parts = []
    if session.topic_summary:
        summary_parts.append(session.topic_summary)
    if session.group_context:
        summary_parts.append(session.group_context)
    if group_memory and session.topic_keywords:
        try:
            relevant = group_memory.search(session.group_id, session.topic_keywords, limit=2)
            for mem in relevant:
                summary_parts.append(mem.content)
        except Exception:
            pass
    if search_result:
        summary_parts.append(f"热梗参考: {search_result}")
    if summary_parts:
        parts.append("[群聊摘要]\n" + "；".join(summary_parts[:5]))

    # ================================================================
    # 5. 格式提醒 — 一行
    # ================================================================
    all_mentionable = [m for m in mentioned_others if m.lower() != speaker_name.lower()]
    for e in entities:
        name = e["profile"].preferred_name or e["name"]
        if name.lower() != speaker_name.lower() and name not in all_mentionable:
            all_mentionable.append(name)
    if all_mentionable:
        parts.append(
            f"[格式] 回复对象 @{speaker_name} 系统自动处理。"
            f"正文提到其他人用 @名字: {', '.join(all_mentionable)}"
        )

    return "\n\n".join(parts)


def _brief_facts(profile) -> str:
    """极简事实摘要，最多 60 字。"""
    if not profile or not profile.known_facts:
        return ""
    # 取前 3 条事实，每条截到 20 字
    items = []
    for k, v in list(profile.known_facts.items())[:3]:
        v_short = v[:20] + ("…" if len(v) > 20 else "")
        items.append(f"{k}={v_short}")
    return ", ".join(items)[:80]


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
        # mention_name 是基准名，优先加入
        if profile.mention_name:
            names.add(profile.mention_name)
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
