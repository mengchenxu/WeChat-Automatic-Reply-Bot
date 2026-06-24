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
    first_seen: float = 0.0
    last_seen: float = 0.0

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
    msg_count: int = 0

    def add_memory(self, text: str, keywords: List[str] = None, category: str = "fact", importance: int = 3) -> GroupMemory:
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

    def search_memories(self, keywords: list, limit: int = 3) -> list:
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
        # Step 5: 找不到 → 创建占位 Person，以后会学到真名
        if name and len(name) >= 2:
            p = self.get_or_create_person(name, name)
            return (p, name)
        return (None, "")

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
    def add_memory(self, room_id: str, text: str, keywords: list = None,
                   category: str = "fact", importance: int = 3) -> GroupMemory:
        g = self.get_group(room_id)
        return g.add_memory(text, keywords, category, importance)

    def search_memories(self, room_id: str, keywords: list, limit: int = 3) -> list:
        g = self._groups.get(room_id)
        if not g:
            return []
        return g.search_memories(keywords, limit)

    # -- 摘要 --
    def update_summary(self, room_id: str, summary: str):
        g = self.get_group(room_id)
        g.context = summary

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
    def to_dict(self) -> dict:
        def fact_to_dict(f: FactEntry) -> dict:
            return {"key": f.key, "value": f.value, "source": f.source,
                    "confidence": f.confidence, "recorded_at": f.recorded_at, "updated_at": f.updated_at}

        def memory_to_dict(m: GroupMemory) -> dict:
            return {"id": m.id, "text": m.text, "keywords": m.keywords,
                    "category": m.category, "importance": m.importance, "timestamp": m.timestamp}

        people = {}
        for wxid, p in self._people.items():
            people[wxid] = {
                "mention_name": p.mention_name, "aliases": p.aliases,
                "facts": [fact_to_dict(f) for f in p.facts],
                "catchphrases": p.catchphrases,
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
                "last_msg_at": g.last_msg_at, "msg_count": g.msg_count,
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
        except (json.JSONDecodeError, IOError):
            logger.exception("加载 Store 失败，从空开始: %s", path)
            return store

        store._meta = data.get("meta", {"version": 1})

        for wxid, d in data.get("people", {}).items():
            p = Person(
                wxid=wxid, mention_name=d.get("mention_name", ""),
                aliases=d.get("aliases", []),
                catchphrases=d.get("catchphrases", []),
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
                msg_count=d.get("msg_count", 0),
            )
            for md in d.get("memories", []):
                g.memories.append(GroupMemory(
                    id=md.get("id", ""), text=md.get("text", ""),
                    keywords=md.get("keywords", []),
                    category=md.get("category", "fact"),
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
