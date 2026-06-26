"""统一数据层 — Person, Group, GroupMemory, ChatMsg + Store"""
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 数据类
# ============================================================
@dataclass
class FactEntry:
    key: str = ""
    value: str = ""
    source: str = "llm_extract"  # user_stated | manual | llm_extract | auto_extract | correction | legacy
    confidence: float = 0.6
    recorded_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        now = time.time()
        if not self.recorded_at:
            self.recorded_at = now
        if not self.updated_at:
            self.updated_at = now


@dataclass
class Person:
    wxid: str
    mention_name: str = ""
    aliases: List[str] = field(default_factory=list)
    facts: List[FactEntry] = field(default_factory=list)
    catchphrases: List[str] = field(default_factory=list)
    relations: Dict[str, str] = field(default_factory=dict)       # wxid → 关系标签（"同事"/"朋友"等）
    speaking_style: str = ""                                       # LLM 生成的个人风格描述
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def preferred_name(self) -> str:
        """向后兼容旧代码——delegate 到 mention_name。"""
        return self.mention_name

    @preferred_name.setter
    def preferred_name(self, value: str):
        if value and not value.startswith("wxid_"):
            self.mention_name = value

    def add_alias(self, name: str):
        name = name.strip()
        if name and name not in self.aliases:
            self.aliases.append(name)

    def add_fact(self, key: str, value: str, source: str = "llm_extract", confidence: float = 0.6) -> bool:
        for f in self.facts:
            if f.key == key:
                if f.value == value:
                    f.updated_at = time.time()
                    return False
                if confidence < f.confidence:
                    return False
                f.value = value
                f.source = source
                f.confidence = confidence
                f.updated_at = time.time()
                return True
        self.facts.append(FactEntry(
            key=key, value=value, source=source, confidence=confidence,
        ))
        return True

    def correct_fact(self, key: str, new_value: str):
        for f in self.facts:
            if f.key == key:
                f.value = new_value
                f.source = "correction"
                f.confidence = 0.95
                f.updated_at = time.time()
                return
        self.facts.append(FactEntry(
            key=key, value=new_value, source="correction", confidence=0.95,
        ))

    def get_fact_strings(self) -> str:
        if not self.facts:
            return ""
        items = []
        for f in self.facts[:3]:
            v = f.value[:20] + ("…" if len(f.value) > 20 else "")
            items.append(f"{f.key}={v}")
        return ", ".join(items)[:80]


@dataclass
class GroupMemory:
    id: str
    text: str
    keywords: List[str] = field(default_factory=list)
    category: str = "fact"
    participants: List[str] = field(default_factory=list)          # 涉及的群友名字
    importance: int = 3
    timestamp: float = 0.0


@dataclass
class ChatMsg:
    role: str  # "user" | "assistant"
    content: str
    sender_name: str = ""
    sender_wxid: str = ""
    timestamp: float = 0.0


@dataclass
class Group:
    room_id: str
    name: str = ""
    context: str = ""
    topic: str = ""
    memories: List[GroupMemory] = field(default_factory=list)
    history: List[ChatMsg] = field(default_factory=list)
    last_msg_at: float = 0.0
    last_reply_at: float = 0.0  # bot 上次回复时间（冷却用）
    msg_count: int = 0
    top_emojis: List[str] = field(default_factory=list)    # 高频表情 top-5
    top_words: List[str] = field(default_factory=list)     # 高频词 top-10
    _emoji_counts: Dict[str, int] = field(default_factory=dict)    # emoji → 计数（不序列化）
    _word_counts: Dict[str, int] = field(default_factory=dict)     # 词 → 计数（不序列化）

    def add_memory(self, text: str, keywords: Optional[List[str]] = None, category: str = "fact", importance: int = 3) -> GroupMemory:
        mid = uuid.uuid4().hex[:12]
        for m in self.memories:
            if m.text.strip() == text.strip():
                m.keywords = list(set(m.keywords + (keywords or [])))
                m.timestamp = time.time()
                return m
        mem = GroupMemory(
            id=mid, text=text, keywords=keywords or [],
            category=category, importance=importance, timestamp=time.time(),
        )
        self.memories.append(mem)
        return mem

    def search_memories(self, keywords: List[str], limit: int = 3) -> List[GroupMemory]:
        now = time.time()
        scored = []
        for m in self.memories:
            mk = set(k.lower() for k in m.keywords)
            qk = set(k.lower() for k in keywords)
            matches = len(mk & qk)
            if matches == 0:
                continue
            days = (now - m.timestamp) / 86400
            score = matches * (0.95 ** days) * (m.importance / 3.0)
            scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]


# ============================================================
# Store
# ============================================================
class Store:
    def __init__(self):
        self._people: Dict[str, Person] = {}
        self._groups: Dict[str, Group] = {}
        self._meta: Dict[str, Any] = {"version": 1, "last_sync": 0}

    # -- Person --
    def get_person(self, wxid: str) -> Optional[Person]:
        return self._people.get(wxid)

    # 向后兼容旧 weflow_client API
    def get(self, wxid: str) -> Optional[Person]:
        return self.get_person(wxid)

    def record_message(self, wxid: str, display_name: str = ""):
        """向后兼容 — 记录一次发言（调用 get_or_create_person）。"""
        self.get_or_create_person(wxid, display_name)

    def get_or_create_person(self, wxid: str, name: str = "") -> Person:
        if wxid not in self._people:
            now = time.time()
            p = Person(wxid=wxid, mention_name=name, first_seen=now, last_seen=now)
            if name and name not in p.aliases:
                p.aliases.append(name)
            self._people[wxid] = p
        else:
            p = self._people[wxid]
            p.last_seen = time.time()
        return p

    # -- Group --
    def get_group(self, room_id: str, name: str = "") -> Group:
        if room_id not in self._groups:
            self._groups[room_id] = Group(room_id=room_id, name=name)
        return self._groups[room_id]

    # -- 名字解析 --
    def find_person_by_name(self, name: str) -> Optional[Person]:
        nl = name.lower().strip()
        if not nl:
            return None
        for p in self._people.values():
            if p.mention_name and nl == p.mention_name.lower():
                return p
        for p in self._people.values():
            for alias in p.aliases:
                if nl == alias.lower():
                    return p
        # 匹配 relations 中的 wxid/标签
        for p in self._people.values():
            for rel_wxid, rel_label in p.relations.items():
                if nl == rel_wxid.lower() or nl == rel_label.lower() or nl in rel_label.lower():
                    return p
        for p in self._people.values():
            if p.mention_name and (nl in p.mention_name.lower() or nl in p.mention_name.lower().replace(" ", "")):
                if self._is_latin_word(p.mention_name, nl, name):
                    return p
        for p in self._people.values():
            for alias in p.aliases:
                if nl in alias.lower() or nl in alias.lower().replace(" ", ""):
                    if self._is_latin_word(alias, nl, name):
                        return p
        return None

    def resolve_name(self, name: str) -> Tuple[Optional[Person], str]:
        """解析名字，返回 (Person, matched_name)。找不到时创建占位 Person。"""
        p = self.find_person_by_name(name)
        if p:
            nl = name.lower().strip()
            for alias in p.aliases:
                if nl == alias.lower():
                    return (p, alias)
            return (p, p.mention_name or name)
        # Step 5: 找不到 → 创建占位 Person（用唯一 key，以后合并到真 wxid）
        if name and len(name) >= 2:
            placeholder_wxid = f"__placeholder__{name}"
            if placeholder_wxid not in self._people:
                p = self.get_or_create_person(placeholder_wxid, name)
            else:
                p = self._people[placeholder_wxid]
            return (p, name)
        return (None, "")

    def scan_aliases_in_text(self, content: str, exclude_wxids: set = None, bot_names: List[str] = None) -> Dict[str, tuple]:
        """扫描正文中出现的已知别名，返回 {wxid: (person, matched_alias)}。"""
        result: Dict[str, tuple] = {}
        exclude = exclude_wxids or set()
        bots = set(bot_names or [])
        for wxid, person in self._people.items():
            if wxid in exclude or person.mention_name in bots:
                continue
            for alias in person.aliases:
                if alias and len(alias) >= 2 and alias in content:
                    if alias in bots:
                        continue
                    result[wxid] = (person, alias)
                    break
        return result

    @staticmethod
    def _is_latin_word(full: str, query_lower: str, original: str) -> bool:
        if not any(c.isascii() and c.isalpha() for c in original):
            return True
        idx = full.lower().find(query_lower)
        if idx > 0 and full[idx - 1].isascii() and full[idx - 1].isalpha():
            return False
        end = idx + len(query_lower)
        if end < len(full) and full[end].isascii() and full[end].isalpha():
            return False
        return True

    # -- 历史管理 --
    def add_to_history(self, room_id: str, msg: ChatMsg):
        g = self.get_group(room_id)
        g.history.append(msg)
        g.last_msg_at = time.time()
        g.msg_count += 1
        if len(g.history) > 20:
            g.history = g.history[-20:]

    def get_history(self, room_id: str, limit: int = 10) -> list:
        g = self._groups.get(room_id)
        if not g:
            return []
        return g.history[-limit:]

    # -- 记忆管理 --
    def add_memory(self, room_id: str, text: str, keywords: Optional[List[str]] = None,
                   category: str = "fact", importance: int = 3) -> GroupMemory:
        g = self.get_group(room_id)
        return g.add_memory(text, keywords, category, importance)

    def search_memories(self, room_id: str, keywords: List[str], limit: int = 3) -> List[GroupMemory]:
        g = self._groups.get(room_id)
        if not g:
            return []
        return g.search_memories(keywords, limit)

    # -- 摘要 --
    def update_summary(self, room_id: str, summary: str):
        g = self.get_group(room_id)
        g.context = summary

    def track_style(self, room_id: str, content: str):
        """实时统计 emoji + 词频，每 10 条更新一次 top-N。"""
        import re as _re
        g = self.get_group(room_id)

        # emoji 统计
        emojis = _re.findall(r'[\U0001F300-\U0001F9FF☀-➿︀-️‍]'
                             r'|[✀-➿]|[︀-️]'
                             r'|©|®|[ -㌀]'
                             r'|[\uD83C-􏰀-\uDFFF]+',
                             content)
        for e in emojis:
            g._emoji_counts[e] = g._emoji_counts.get(e, 0) + 1

        # 词频统计（CJK 双字词 + 拉丁词）
        cjk_words = _re.findall(r'[一-鿿]{2,4}', content)
        latin_words = _re.findall(r'[a-zA-Z]{3,}', content)
        for w in cjk_words + latin_words:
            w = w.lower()
            g._word_counts[w] = g._word_counts.get(w, 0) + 1

        # 每 10 条更新 top-N
        if g.msg_count > 0 and g.msg_count % 10 == 0:
            g.top_emojis = [e for e, _ in sorted(g._emoji_counts.items(),
                            key=lambda x: -x[1])[:5]]
            g.top_words = [w for w, _ in sorted(g._word_counts.items(),
                           key=lambda x: -x[1])[:10]]

    def cleanup_old_memories(self, room_id: str, max_age_days: int = 30):
        """清理超过 max_age_days 天且重要度 ≤ 2 的记忆。"""
        if room_id not in self._groups:
            return
        g = self._groups[room_id]
        now = time.time()
        cutoff = now - max_age_days * 86400
        before = len(g.memories)
        g.memories = [
            m for m in g.memories
            if not (m.timestamp < cutoff and m.importance <= 2)
        ]
        removed = before - len(g.memories)
        if removed:
            logger.info("Cleaned up %d old memories (room=%s)", removed, room_id[:20])

    # -- 批量变更 --
    def apply_mutations(self, mutations: dict):
        for wxid, facts in mutations.get("add_facts", {}).items():
            p = self.get_or_create_person(wxid)
            for key, value, source, conf in facts:
                p.add_fact(key, value, source, conf)

        for room_id, mems in mutations.get("add_memories", {}).items():
            for m in mems:
                self.add_memory(room_id, m["text"], m.get("keywords", []),
                              m.get("category", "fact"), m.get("importance", 3))

        for room_id, summary in mutations.get("update_summary", {}).items():
            self.update_summary(room_id, summary)

        for wxid, aliases in mutations.get("add_aliases", {}).items():
            p = self.get_or_create_person(wxid)
            for alias in aliases:
                p.add_alias(alias)

        for wxid, updates in mutations.get("correct_facts", {}).items():
            p = self.get_or_create_person(wxid)
            for key, new_value in updates:
                p.correct_fact(key, new_value)

    # ================================================================
    # JSON Save/Load
    # ================================================================
    def to_dict(self) -> Dict[str, Any]:
        def fact_to_dict(f: FactEntry) -> Dict[str, Any]:
            return {"key": f.key, "value": f.value, "source": f.source,
                    "confidence": f.confidence, "recorded_at": f.recorded_at, "updated_at": f.updated_at}

        def memory_to_dict(m: GroupMemory) -> Dict[str, Any]:
            return {"id": m.id, "text": m.text, "keywords": m.keywords,
                    "category": m.category, "participants": m.participants,
                    "importance": m.importance, "timestamp": m.timestamp}

        people = {}
        for wxid, p in self._people.items():
            people[wxid] = {
                "mention_name": p.mention_name, "aliases": p.aliases,
                "facts": [fact_to_dict(f) for f in p.facts],
                "catchphrases": p.catchphrases,
                "relations": p.relations,
                "speaking_style": p.speaking_style,
                "first_seen": p.first_seen, "last_seen": p.last_seen,
            }

        groups = {}
        for rid, g in self._groups.items():
            groups[rid] = {
                "name": g.name, "context": g.context, "topic": g.topic,
                "memories": [memory_to_dict(m) for m in g.memories],
                "history": [
                    {"role": h.role, "content": h.content, "sender_name": h.sender_name,
                     "sender_wxid": h.sender_wxid, "timestamp": h.timestamp}
                    for h in g.history[-20:]
                ],
                "last_msg_at": g.last_msg_at, "last_reply_at": g.last_reply_at,
                "msg_count": g.msg_count,
                "top_emojis": g.top_emojis, "top_words": g.top_words,
            }

        return {"meta": self._meta, "people": people, "groups": groups}

    def save(self, path: str = "data/store.json"):
        try:
            dirname = os.path.dirname(path)
            if dirname:  # 避免 path="store.json" 时 os.makedirs("") 崩溃
                os.makedirs(dirname, exist_ok=True)
            data = self.to_dict()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            # 清理残留 tmp 文件
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except Exception:
                pass
            logger.exception("保存 Store 失败: %s", path)

    @classmethod
    def load(cls, path: str = "data/store.json") -> "Store":
        store = cls()
        if not os.path.exists(path):
            return store
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.exception("加载 Store 失败，从空开始: %s", path)
            return store

        store._meta = data.get("meta", {"version": 1})

        for wxid, d in data.get("people", {}).items():
            p = Person(
                wxid=wxid, mention_name=d.get("mention_name", ""),
                aliases=d.get("aliases", []),
                catchphrases=d.get("catchphrases", []),
                relations=d.get("relations", {}),
                speaking_style=d.get("speaking_style", ""),
                first_seen=d.get("first_seen", 0.0),
                last_seen=d.get("last_seen", 0.0),
            )
            for fd in d.get("facts", []):
                p.facts.append(FactEntry(
                    key=fd.get("key", ""), value=fd.get("value", ""),
                    source=fd.get("source", "legacy"),
                    confidence=fd.get("confidence", 0.5),
                    recorded_at=fd.get("recorded_at", 0.0),
                    updated_at=fd.get("updated_at", 0.0),
                ))
            store._people[wxid] = p

        for rid, d in data.get("groups", {}).items():
            g = Group(
                room_id=rid, name=d.get("name", ""),
                context=d.get("context", ""), topic=d.get("topic", ""),
                last_msg_at=d.get("last_msg_at", 0.0),
                last_reply_at=d.get("last_reply_at", 0.0),
                msg_count=d.get("msg_count", 0),
                top_emojis=d.get("top_emojis", []),
                top_words=d.get("top_words", []),
            )
            for md in d.get("memories", []):
                g.memories.append(GroupMemory(
                    id=md.get("id", ""), text=md.get("text", ""),
                    keywords=md.get("keywords", []),
                    category=md.get("category", "fact"),
                    participants=md.get("participants", []),
                    importance=md.get("importance", 3),
                    timestamp=md.get("timestamp", 0.0),
                ))
            for hd in d.get("history", []):
                g.history.append(ChatMsg(
                    role=hd.get("role", "user"), content=hd.get("content", ""),
                    sender_name=hd.get("sender_name", ""),
                    sender_wxid=hd.get("sender_wxid", ""),
                    timestamp=hd.get("timestamp", 0.0),
                ))
            store._groups[rid] = g

        return store

    # ================================================================
    # 旧数据迁移
    # ================================================================
    @classmethod
    def migrate_from_old_files(cls, store_path: str = "data/store.json",
                                data_dir: str = "data") -> "Store":
        """从旧的 users.json / group_memories.json 迁移数据到 Store。幂等。"""
        import os as _os

        store = cls.load(store_path)
        migrated = False

        # 1. 迁移 users.json → Person
        users_path = _os.path.join(data_dir, "users.json")
        if _os.path.exists(users_path) and not _os.path.exists(users_path + ".bak"):
            try:
                with open(users_path, "r", encoding="utf-8") as f:
                    users_data = json.load(f)
                for wxid, d in users_data.items():
                    p = store.get_or_create_person(wxid, d.get("mention_name", "") or d.get("preferred_name", ""))
                    # 迁移 aliases
                    for alias in d.get("aliases", []):
                        p.add_alias(alias)
                    for dn in d.get("display_names", []):
                        if dn and dn != p.mention_name:
                            p.add_alias(dn)
                    # 迁移 facts
                    for key, fd in d.get("known_facts", {}).items():
                        if isinstance(fd, dict):
                            p.add_fact(key, fd.get("value", ""),
                                       source=fd.get("source", "legacy"),
                                       confidence=fd.get("confidence", 0.5))
                        else:
                            p.add_fact(key, str(fd), source="legacy", confidence=0.5)
                    # 迁移 relations
                    for rel_wxid, rel_label in d.get("relations", {}).items():
                        p.relations[rel_wxid] = rel_label
                    # 迁移 speaking_style
                    if d.get("speaking_style"):
                        p.speaking_style = d["speaking_style"]
                    # 迁移 catchphrases
                    for cp in d.get("catchphrases", []):
                        if cp not in p.catchphrases:
                            p.catchphrases.append(cp)
                _os.rename(users_path, users_path + ".bak")
                logger.info("Migrated %d users from %s", len(users_data), users_path)
                migrated = True
            except Exception:
                logger.exception("Failed to migrate %s, skipping", users_path)

        # 2. 迁移 group_memories.json → Group
        mem_path = _os.path.join(data_dir, "group_memories.json")
        if _os.path.exists(mem_path) and not _os.path.exists(mem_path + ".bak"):
            try:
                with open(mem_path, "r", encoding="utf-8") as f:
                    mem_data = json.load(f)
                for room_id, mems in mem_data.items():
                    g = store.get_group(room_id)
                    for m in mems:
                        g.add_memory(
                            text=m.get("content", m.get("text", "")),
                            keywords=m.get("keywords", []),
                            category=m.get("category", "fact"),
                            importance=m.get("importance", 3),
                        )
                _os.rename(mem_path, mem_path + ".bak")
                logger.info("Migrated %d groups memories from %s", len(mem_data), mem_path)
                migrated = True
            except Exception:
                logger.exception("Failed to migrate %s, skipping", mem_path)

        if migrated:
            store.save(store_path)
            logger.info("Migration complete, saved to %s", store_path)

        return store
