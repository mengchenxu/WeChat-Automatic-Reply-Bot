"""回复解码阶段 — 提取 @mentions, /remember, /context, 纠正信号"""
import re
from dataclasses import dataclass, field
from typing import Dict, List

from src.store import Store
from src.enrich import EnrichedCtx


@dataclass
class DecodedReply:
    clean_text: str              # 清理后的回复文本
    at_mentions: List[str] = field(default_factory=list)  # 内联 @ 的名字
    mutations: dict = field(default_factory=dict)          # Store 变更


# 纠正信号正则
_CORRECTION_PATTERNS = [
    r'(?:我|俺)不(?:是|叫)\s*(.+?)(?:[，。！\s]|$)',
    r'(?:谁|哪个|哪)告诉?你\s*(?:我|俺)\s*(?:是|叫)\s*(.+?)(?:[，。！\s]|$)',
    r'你记错了',
    r'我没说过',
]


def decode(raw_reply: str, enriched: EnrichedCtx, store: Store) -> DecodedReply:
    """解码 LLM 回复：提取 @mentions, 指令, 纠正信号。返回 DecodedReply。"""
    mutations: dict = {}
    text = raw_reply

    # --- /remember 指令 ---
    text, remember_mutations = _extract_remember(text, enriched, store)
    if remember_mutations:
        mutations.update(remember_mutations)

    # --- /context 指令 ---
    text, context_update = _extract_context(text, enriched)
    if context_update:
        mutations.setdefault("update_summary", {})[enriched.parsed.room_id] = context_update

    # --- 纠正信号 ---
    text, correction_mutations = _detect_correction(text, enriched, store)
    if correction_mutations:
        mutations.update(correction_mutations)

    # --- @mention 提取 ---
    at_mentions: List[str] = []
    sender_name = enriched.parsed.sender_name or ""

    # 0. 先处理 @wxid_xxx 模式（正则匹配不到下划线，用 wxid 直接查找）
    text = _resolve_wxid_mentions(text, store, at_mentions)

    # 匹配拉丁名 + 中文名
    latin = re.findall(r'@([a-zA-Z][a-zA-Z0-9 ]*(?:\s+[a-zA-Z][a-zA-Z0-9 ]*)*)', text)
    cjk = re.findall(r'@([一-鿿぀-ゟ가-힯]{2,4})', text)
    all_names = list(set(latin + cjk))

    for name in all_names:
        name = name.strip()
        if not name or name.lower() == sender_name.lower():
            continue
        person, matched = store.resolve_name(name)
        if person:
            real = person.mention_name or matched or name
            if real not in at_mentions:
                at_mentions.append(real)
                # 自动学习外号
                if name not in person.aliases:
                    person.add_alias(name)
                    mutations.setdefault("add_aliases", {}).setdefault(person.wxid, []).append(name)

    # --- 清理开头 @发送者 ---
    clean = text
    if sender_name:
        clean = re.sub(rf'^@?\s*{re.escape(sender_name)}\s*[,，]?\s*', '', clean).strip()

    # --- 兜底：清除所有未解析为人类可读名的 @mention ---
    # 任何 @xxx 如果不在 at_mentions 中，从 clean_text 中移除
    all_known = set(at_mentions)
    if sender_name:
        all_known.add(sender_name)

    def _strip_unresolved(m: re.Match) -> str:
        name = m.group(0)[1:]  # 去掉 @
        # 如果已被解析为已知名字 → 保留
        if name.strip() in all_known:
            return m.group(0)
        # wxid 形式 → 移除
        if re.match(r'^(wxid_|[a-z][a-z0-9_]{5,})$', name.strip()):
            return ""
        # 查不到 → 移除
        person2, _ = store.resolve_name(name.strip())
        if person2 and person2.mention_name:
            return m.group(0)
        return ""

    clean = re.sub(r'@\S+', _strip_unresolved, clean)
    clean = re.sub(r'\s{2,}', ' ', clean).strip()

    return DecodedReply(clean_text=clean, at_mentions=at_mentions, mutations=mutations)


def _extract_remember(text: str, enriched: EnrichedCtx, store: Store) -> tuple[str, dict]:
    """提取 /remember @name key: value 指令。"""
    mutations: dict = {}
    pattern = r'/remember\s+(?:@(\S+)\s+)?(.+?)\s*:\s*(.+)'

    def _replacer(m: re.Match) -> str:
        at_name = (m.group(1) or "").strip()
        key = m.group(2).strip()
        value = m.group(3).strip()
        if not key or not value:
            return m.group(0)

        if at_name:
            person, _ = store.resolve_name(at_name)
            if person:
                _apply_remember(person, key, value, mutations)
        else:
            wxid = enriched.parsed.sender_wxid
            person = store.get_person(wxid)
            if person:
                _apply_remember(person, key, value, mutations)
        return ""

    clean = re.sub(pattern, _replacer, text)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip(), mutations


def _extract_context(text: str, enriched: EnrichedCtx) -> tuple[str, str | None]:
    """提取 /context 群背景更新指令。"""
    pattern = r'/context\s+(.+?)(?:\n|$)'
    context_text: str | None = None

    def _replacer(m: re.Match) -> str:
        nonlocal context_text
        context_text = m.group(1).strip()
        return ""

    clean = re.sub(pattern, _replacer, text)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip(), context_text


def _resolve_wxid_mentions(text: str, store, at_mentions: List[str]) -> str:
    """处理 @wxid 模式：wxid_ 前缀 + 无前缀小写数字型 wxid → 替换为 mention_name 或移除。"""
    import re as _re
    # 模式1: wxid_ 前缀
    # 模式2: 无前缀 wxid（小写字母开头 + 数字，无大写，长度 ≥ 8）
    pattern = _re.compile(r'@((?:wxid_[a-zA-Z0-9_]+)|(?:[a-z][a-z0-9_]{6,}))')

    def _replacer(m):
        wxid = m.group(1)
        person = store.get_person(wxid)
        if person and person.mention_name:
            if person.mention_name not in at_mentions:
                at_mentions.append(person.mention_name)
            return f"@{person.mention_name}"
        # 找不到则去掉（不能让它出现在正文）
        return ""

    return pattern.sub(_replacer, text)


def _apply_remember(person, key: str, value: str, mutations: dict):
    """根据 key 类型写入事实或外号。"""
    if key.strip() in ("外号", "别名", "绰号"):
        person.add_alias(value)
        mutations.setdefault("add_aliases", {}).setdefault(person.wxid, []).append(value)
    else:
        person.add_fact(key, value, source="manual", confidence=0.8)
        mutations.setdefault("add_facts", {}).setdefault(person.wxid, []).append(
            (key, value, "manual", 0.8))


def _detect_correction(text: str, enriched: EnrichedCtx, store: Store) -> tuple[str, dict]:
    """检测纠正信号（'我不叫xxx', '你记错了' 等）。"""
    mutations: dict = {}
    wxid = enriched.parsed.sender_wxid

    for pat in _CORRECTION_PATTERNS:
        for m in re.finditer(pat, text):
            group1 = m.group(1) if m.lastindex and m.lastindex >= 1 else None
            matched = m.group(0)
            person = store.get_person(wxid)
            if not person:
                continue
            if group1 and len(group1) <= 10:
                # "我不叫小乐" → 找 facts 中包含旧值的 key
                old_value = group1
                found_key = None
                for f in person.facts:
                    if old_value in f.value:
                        found_key = f.key
                        break
                if not found_key:
                    found_key = "名字"
                person.correct_fact(found_key, old_value)
                mutations.setdefault("correct_facts", {}).setdefault(wxid, []).append(
                    (found_key, old_value))
            elif matched in ("你记错了", "我没说过"):
                # 模糊纠正——标记最新事实为待修正
                if person.facts:
                    latest = person.facts[-1]
                    latest.source = "needs_review"
    return text, mutations
