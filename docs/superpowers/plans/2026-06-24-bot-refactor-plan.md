# Bot Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the WeChat bot into a clean six-stage pipeline with unified data store, TDD from the ground up.

**Architecture:** Pipeline model — Parse → Enrich → Prompt → LLM → Decode → Send. Each stage is a pure function. `Store` is the single shared state (one JSON file). WeFlow + UIA + DeepSeek API layers preserved.

**Tech Stack:** Python 3.12+, pytest, dataclasses, openai SDK, WeFlow REST API, Windows UIA (ctypes)

## Global Constraints

- Python 3.12+, all type hints explicit
- Each src file ≤ 200 lines
- Store as single JSON file (`data/store.json`), atomic write (temp → rename)
- TDD: test first, fail, implement, pass, commit
- mention_name is canonical, never replaced by wxid
- All @mentions inline (not bunched at start)
- System prompt ≤ 150 words
- LLM context: 10 message history max, 1-2 line summary
- Fallback replies never expose technical details

---

## File Map

| File | Responsibility | Lines (target) |
|------|---------------|----------------|
| `src/store.py` | Person/Group/Memory dataclasses, Store CRUD, JSON save/load | ~180 |
| `src/parse.py` | Message parsing: extract sender, @mentions, commands, strip separators | ~60 |
| `src/enrich.py` | Name resolution, memory retrieval, context assembly | ~100 |
| `src/prompt.py` | Four-section prompt builder (system + summary + history + current) | ~60 |
| `src/llm.py` | DeepSeek API call, tool use (search_web), retry/fallback | ~100 |
| `src/decode.py` | Reply parsing: @mentions, /remember, /context, correction signals | ~80 |
| `src/send.py` | UIA inline @mention sender | ~100 |
| `src/pipeline.py` | Main loop orchestrating all six stages | ~120 |
| `src/config.py` | Config YAML loader (simplified from current) | ~40 |
| `src/weflow.py` | WeFlow REST client (poll + contacts API), moved from weflow_client.py | ~120 |
| `src/uia.py` | UIA keyboard simulation, moved from uia_sender.py | ~80 |
| `main.py` | Entry point: setup logging, load config, start pipeline | ~50 |

**Tests:** `tests/` directory, one test file per source file.

---

### Task 1: Store Data Model (Person, Group, Memory)

**Files:**
- Create: `src/store.py`
- Create: `tests/test_store.py`

**Interfaces:**
- Produces: `Person`, `Group`, `GroupMemory`, `ChatMsg` dataclasses
- Produces: `Store` class skeleton with `load()`, `save()`, `get_person()`, `get_or_create_person()`, `get_group()`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
import pytest
from src.store import Store, Person, Group, ChatMsg, GroupMemory


def test_person_mention_name_is_canonical():
    p = Person(wxid="wxid_abc", mention_name="子南")
    assert p.mention_name == "子南"
    assert p.aliases == []


def test_person_add_alias():
    p = Person(wxid="wxid_abc", mention_name="子南")
    p.add_alias("南南")
    assert "南南" in p.aliases
    # 去重
    p.add_alias("南南")
    assert len(p.aliases) == 1


def test_person_add_fact():
    p = Person(wxid="wxid_abc", mention_name="子南")
    p.add_fact("xp系统", "喜欢人马片", source="llm_extract", confidence=0.6)
    assert len(p.facts) == 1
    assert p.facts[0].value == "喜欢人马片"


def test_person_add_fact_low_confidence_blocked():
    p = Person(wxid="wxid_abc")
    p.add_fact("立场", "支持猎鹰", source="user_stated", confidence=0.9)
    # 低置信度不能覆盖高置信度
    result = p.add_fact("立场", "不支持猎鹰", source="llm_extract", confidence=0.6)
    assert result is False
    assert p.facts[0].value == "支持猎鹰"


def test_group_create():
    g = Group(room_id="123@chatroom", name="测试群")
    assert g.memories == []
    assert g.context == ""


def test_chat_msg():
    msg = ChatMsg(role="user", content="你好", sender_name="贯一", sender_wxid="wxid_123", timestamp=1234567890)
    assert msg.role == "user"
    assert msg.sender_name == "贯一"


def test_store_get_or_create_person():
    store = Store()
    p = store.get_or_create_person("wxid_new", "新人")
    assert p.wxid == "wxid_new"
    assert p.mention_name == "新人"
    assert store.get_person("wxid_new") is p


def test_store_get_group():
    store = Store()
    g = store.get_group("123@chatroom", "测试群")
    assert g.room_id == "123@chatroom"
    assert g.name == "测试群"
    # 幂等
    g2 = store.get_group("123@chatroom")
    assert g2 is g
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/chatbot && python -m pytest tests/test_store.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'src.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/store.py
"""统一数据层 — Person, Group, GroupMemory, ChatMsg + Store"""
import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FactEntry:
    value: str
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
    aliases: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    catchphrases: list = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0

    def add_alias(self, name: str):
        name = name.strip()
        if name and name not in self.aliases:
            self.aliases.append(name)

    def add_fact(self, key: str, value: str, source: str = "llm_extract", confidence: float = 0.6) -> bool:
        # 检查是否已有同 key 的事实
        for f in self.facts:
            if f.key == key:
                if f.value == value:
                    f.updated_at = time.time()
                    return False
                if confidence < f.confidence:
                    return False  # 低置信度不能覆盖高置信度
                # 覆盖
                f.value = value
                f.source = source
                f.confidence = confidence
                f.updated_at = time.time()
                return True
        # 新事实
        self.facts.append(FactEntry(
            value=value, source=source, confidence=confidence,
        ))
        return True

    def correct_fact(self, key: str, new_value: str):
        """强制修正（纠正信号），无视置信度。"""
        for f in self.facts:
            if f.key == key:
                f.value = new_value
                f.source = "correction"
                f.confidence = 0.95
                f.updated_at = time.time()
                return
        self.facts.append(FactEntry(
            value=new_value, source="correction", confidence=0.95,
        ))

    def get_fact_strings(self) -> str:
        """极简摘要，最多 80 字。"""
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
    keywords: list = field(default_factory=list)
    category: str = "fact"  # event | decision | fact | joke | topic_change
    importance: int = 3
    timestamp: float = 0.0


@dataclass
class Group:
    room_id: str
    name: str = ""
    context: str = ""
    topic: str = ""
    memories: list = field(default_factory=list)
    history: list = field(default_factory=list)  # list[ChatMsg]
    last_msg_at: float = 0.0
    msg_count: int = 0

    def add_memory(self, text: str, keywords: list = None, category: str = "fact", importance: int = 3):
        import hashlib
        mid = hashlib.md5(f"{self.room_id}:{text}:{time.time()}".encode()).hexdigest()[:12]
        # 去重
        for m in self.memories:
            if m.text.strip() == text.strip():
                m.keywords = list(set(m.keywords + (keywords or [])))
                m.timestamp = time.time()
                return m
        mem = GroupMemory(id=mid, text=text, keywords=keywords or [], category=category,
                         importance=importance, timestamp=time.time())
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


@dataclass
class ChatMsg:
    role: str  # "user" | "assistant"
    content: str
    sender_name: str = ""
    sender_wxid: str = ""
    timestamp: float = 0.0


class Store:
    def __init__(self):
        self._people: Dict[str, Person] = {}
        self._groups: Dict[str, Group] = {}
        self._meta: dict = {"version": 1}

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

    # -- Save/Load (skeleton) --
    def save(self, path: str = "data/store.json"):
        pass  # 后续 Task 实现

    @classmethod
    def load(cls, path: str = "data/store.json") -> "Store":
        return cls()  # 后续 Task 实现
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/chatbot && python -m pytest tests/test_store.py -v
```
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_store.py
git commit -m "feat: Store data model — Person, Group, Memory, ChatMsg with fact confidence"
```

---

### Task 2: Store JSON Save/Load + Atomic Write

**Files:**
- Modify: `src/store.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Produces: `Store.save(path)` — atomic write via temp file + rename
- Produces: `Store.load(path)` — reads JSON, returns Store
- Consumes: `Person`, `Group`, `GroupMemory`, `ChatMsg` from Task 1

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_store.py
def test_store_save_load_roundtrip(tmp_path):
    store = Store()
    p = store.get_or_create_person("wxid_abc", "子南")
    p.add_fact("xp", "人马片", source="llm_extract", confidence=0.6)
    g = store.get_group("123@chatroom", "测试群")
    g.add_memory("子南喜欢人马片", keywords=["子南", "人马"])
    g.history.append(ChatMsg(role="user", content="你好", sender_name="子南", sender_wxid="wxid_abc"))

    path = tmp_path / "store.json"
    store.save(str(path))

    store2 = Store.load(str(path))
    p2 = store2.get_person("wxid_abc")
    assert p2.mention_name == "子南"
    assert len(p2.facts) == 1
    assert p2.facts[0].value == "人马片"

    g2 = store2.get_group("123@chatroom")
    assert len(g2.memories) == 1
    assert g2.memories[0].text == "子南喜欢人马片"
    assert len(g2.history) == 1


def test_store_load_nonexistent_returns_empty():
    store = Store.load("data/nonexistent.json")
    assert len(store._people) == 0
    assert len(store._groups) == 0


def test_save_overwrites_existing(tmp_path):
    path = tmp_path / "store.json"
    s1 = Store()
    s1.get_or_create_person("wxid_x", "test")
    s1.save(str(path))

    s2 = Store()
    s2.get_or_create_person("wxid_y", "other")
    s2.save(str(path))

    s3 = Store.load(str(path))
    assert s3.get_person("wxid_y") is not None
    assert s3.get_person("wxid_x") is None  # 被覆盖
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/chatbot && python -m pytest tests/test_store.py::test_store_save_load_roundtrip -v
```
Expected: FAIL — `AssertionError` (save 是空方法)

- [ ] **Step 3: Implement save/load**

```python
# 替换 Store 类的 save/load 方法
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
                for h in g.history[-20:]  # 只保留最近 20 条历史
            ],
            "last_msg_at": g.last_msg_at, "msg_count": g.msg_count,
        }

    return {"meta": self._meta, "people": people, "groups": groups}


def save(self, path: str = "data/store.json"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = self.to_dict()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子替换


@classmethod
def load(cls, path: str = "data/store.json") -> "Store":
    store = cls()
    if not os.path.exists(path):
        return store
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

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
```

**注意：** `FactEntry` 需要加一个 `key` 字段。回 Task 1 补充。

- [ ] **Step 3.5: Add `key` field to FactEntry**

```python
# 修改 FactEntry dataclass
@dataclass
class FactEntry:
    key: str = ""
    value: str = ""
    source: str = "llm_extract"
    confidence: float = 0.6
    recorded_at: float = 0.0
    updated_at: float = 0.0
```

更新 `Person.add_fact` 中的 `FactEntry(...)` 构造加上 `key=key`。

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/chatbot && python -m pytest tests/test_store.py -v
```
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_store.py
git commit -m "feat: Store JSON save/load with atomic write"
```

---

### Task 3: Store Name Resolution

**Files:**
- Modify: `src/store.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Produces: `Store.resolve_name(name: str) -> tuple[Optional[Person], str]` — 返回 (Person, 匹配到的名字) 或 (None, "")
- Produces: `Store.find_person_by_name(name: str) -> Optional[Person]`

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_store.py
def test_resolve_name_exact_mention():
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    person, matched = store.resolve_name("子南")
    assert person is p
    assert matched == "子南"


def test_resolve_name_alias():
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.add_alias("南南")
    person, matched = store.resolve_name("南南")
    assert person is p
    assert matched == "南南"


def test_resolve_name_substring():
    store = Store()
    store.get_or_create_person("wxid_a", "子南")
    store.get_or_create_person("wxid_b", "B L U E")
    # 子串匹配
    person, _ = store.resolve_name("blue")
    assert person.wxid == "wxid_b"


def test_resolve_name_not_found():
    store = Store()
    person, matched = store.resolve_name("不存在的人")
    assert person is None


def test_find_person_by_name():
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.add_alias("阿南")
    assert store.find_person_by_name("子南") is p
    assert store.find_person_by_name("阿南") is p
    assert store.find_person_by_name("不存在") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/chatbot && python -m pytest tests/test_store.py::test_resolve_name_exact_mention -v
```
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'resolve_name'`

- [ ] **Step 3: Implement name resolution**

```python
# 添加到 Store 类
def find_person_by_name(self, name: str) -> Optional[Person]:
    """按名字查找用户，优先级：mention_name > aliases > 子串"""
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
    # 子串匹配（拉丁名用词边界检查）
    for p in self._people.values():
        if p.mention_name and nl in p.mention_name.lower():
            if self._is_latin_word(p.mention_name, nl, name):
                return p
    for p in self._people.values():
        for alias in p.aliases:
            if nl in alias.lower():
                if self._is_latin_word(alias, nl, name):
                    return p
    return None


def resolve_name(self, name: str) -> tuple:
    """解析名字，返回 (Person, matched_name) 或 (None, '')"""
    p = self.find_person_by_name(name)
    if p:
        # 返回最佳显示名：mention_name
        return (p, p.mention_name or name)
    return (None, "")


@staticmethod
def _is_latin_word(full: str, query_lower: str, original: str) -> bool:
    """检查子串匹配是否在词边界上（仅对拉丁名字有效）"""
    if not any(c.isascii() and c.isalpha() for c in original):
        return True  # 非拉丁名不做词边界检查
    idx = full.lower().find(query_lower)
    if idx > 0 and full[idx - 1].isascii() and full[idx - 1].isalpha():
        return False
    end = idx + len(query_lower)
    if end < len(full) and full[end].isascii() and full[end].isalpha():
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/chatbot && python -m pytest tests/test_store.py -v
```
Expected: PASS (19 tests)

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_store.py
git commit -m "feat: Store name resolution — find_person_by_name + resolve_name"
```

---

### Task 4: Store History & Memory Management

**Files:**
- Modify: `src/store.py`
- Modify: `tests/test_store.py`

**Interfaces:**
- Produces: `Store.add_to_history(room_id, msg: ChatMsg)`
- Produces: `Store.get_history(room_id, limit=10) -> list[ChatMsg]`
- Produces: `Store.add_memory(room_id, text, keywords, category)`
- Produces: `Store.search_memories(room_id, keywords, limit=3) -> list[GroupMemory]`
- Produces: `Store.update_summary(room_id, summary: str)`
- Produces: `Store.apply_mutations(mutations: dict)` — 批量应用 decode 阶段的变更

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_store.py
def test_add_to_history():
    store = Store()
    store.get_group("123@chatroom", "test")
    msg = ChatMsg(role="user", content="你好", sender_name="贯一", sender_wxid="wxid_a")
    store.add_to_history("123@chatroom", msg)
    history = store.get_history("123@chatroom")
    assert len(history) == 1
    assert history[0].content == "你好"


def test_get_history_limit():
    store = Store()
    store.get_group("123@chatroom", "test")
    for i in range(15):
        store.add_to_history("123@chatroom", ChatMsg(role="user", content=f"msg{i}", sender_name="test"))
    history = store.get_history("123@chatroom", limit=10)
    assert len(history) == 10
    # 应该是最新的 10 条
    assert history[-1].content == "msg14"


def test_add_and_search_memories():
    store = Store()
    store.get_group("123@chatroom", "test")
    store.add_memory("123@chatroom", "子南喜欢人马片", keywords=["子南", "人马"], category="joke")
    results = store.search_memories("123@chatroom", ["子南"])
    assert len(results) == 1
    assert results[0].text == "子南喜欢人马片"


def test_update_summary():
    store = Store()
    store.get_group("123@chatroom")
    store.update_summary("123@chatroom", "群内在聊CS比赛")
    g = store.get_group("123@chatroom")
    assert g.context == "群内在聊CS比赛"


def test_apply_mutations():
    store = Store()
    mutations = {
        "add_facts": {"wxid_a": [("xp", "人马片", "llm_extract", 0.6)]},
        "add_memories": {"123@chatroom": [{"text": "子南看番号", "keywords": ["子南"], "category": "event"}]},
        "update_summary": {"123@chatroom": "新摘要"},
        "add_aliases": {"wxid_a": ["阿南"]},
    }
    store.apply_mutations(mutations)
    p = store.get_or_create_person("wxid_a", "子南")
    assert len(p.facts) == 1
    assert p.facts[0].value == "人马片"
    assert "阿南" in p.aliases
    g = store.get_group("123@chatroom")
    assert g.context == "新摘要"
    assert len(g.memories) == 1
```

- [ ] **Step 3: Implement methods**

```python
# 添加到 Store 类
def add_to_history(self, room_id: str, msg: ChatMsg):
    g = self.get_group(room_id)
    g.history.append(msg)
    g.last_msg_at = time.time()
    g.msg_count += 1
    # 历史最多保留 20 条
    if len(g.history) > 20:
        g.history = g.history[-20:]


def get_history(self, room_id: str, limit: int = 10) -> list:
    g = self._groups.get(room_id)
    if not g:
        return []
    return g.history[-limit:]


def add_memory(self, room_id: str, text: str, keywords: list = None,
               category: str = "fact", importance: int = 3) -> GroupMemory:
    g = self.get_group(room_id)
    return g.add_memory(text, keywords, category, importance)


def search_memories(self, room_id: str, keywords: list, limit: int = 3) -> list:
    g = self._groups.get(room_id)
    if not g:
        return []
    return g.search_memories(keywords, limit)


def update_summary(self, room_id: str, summary: str):
    g = self.get_group(room_id)
    g.context = summary


def apply_mutations(self, mutations: dict):
    """批量应用 store 变更（来自 decode 阶段）。"""
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/chatbot && python -m pytest tests/test_store.py -v
```
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_store.py
git commit -m "feat: Store history/memory/summary management + apply_mutations"
```

---

### Task 5: Parse Stage

**Files:**
- Create: `src/parse.py`
- Create: `tests/test_parse.py`

**Interfaces:**
- Produces: `ParsedMsg` dataclass
- Produces: `parse(msg_data: dict, bot_names: list) -> ParsedMsg`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parse.py
import pytest
from src.parse import parse, ParsedMsg


def make_msg(content: str, raw_content: str = "", sender: str = "wxid_test") -> dict:
    return {
        "content": content,
        "rawContent": raw_content or f"{sender}:\n{content}",
        "senderUsername": sender,
        "localId": "msg_001",
        "createTime": 1234567890,
        "talker": "123@chatroom",
    }


def test_parse_simple_message():
    msg = make_msg("你好", sender="wxid_a")
    result = parse(msg, bot_names=["鼠鼠"])
    assert result.sender_wxid == "wxid_a"
    assert result.content == "你好"
    assert result.is_at_bot is False
    assert result.is_command is False


def test_parse_at_bot():
    msg = make_msg("@鼠鼠 你好", sender="wxid_a")
    result = parse(msg, bot_names=["鼠鼠"])
    assert result.is_at_bot is True
    # @bot 被去掉
    assert "鼠鼠" not in result.content


def test_parse_at_bot_and_other():
    msg = make_msg("@鼠鼠 @子南  你认识他吗", sender="wxid_a")
    result = parse(msg, bot_names=["鼠鼠"])
    assert result.is_at_bot is True
    # @子南 被保留
    assert "@子南" in result.content
    assert "子南" in result.raw_mentions


def test_parse_command():
    msg = make_msg("@鼠鼠 /help", sender="wxid_a")
    result = parse(msg, bot_names=["鼠鼠"])
    assert result.is_command
    assert result.command == "/help"


def test_parse_strips_wechat_separator():
    msg = make_msg("@鼠鼠 你好世界", sender="wxid_a")
    result = parse(msg, bot_names=["鼠鼠"])
    assert " " not in result.content
    assert result.content == "你好世界"


def test_parse_extracts_mentions():
    msg = make_msg("@鼠鼠 @贯一 @B L U E  都来", sender="wxid_a")
    result = parse(msg, bot_names=["鼠鼠"])
    # 包含贯一和B L U E，不包含鼠鼠
    assert "贯一" in result.raw_mentions
    assert "B L U E" in result.raw_mentions
    assert "鼠鼠" not in result.raw_mentions
```

- [ ] **Step 3: Implement**

```python
# src/parse.py
"""消息解析阶段 — 提取 sender, @mentions, 命令, 去分隔符"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedMsg:
    room_id: str
    sender_wxid: str
    sender_name: str
    content: str            # 已清理：去@bot、去 分隔符，保留其他@mention
    raw_mentions: list = field(default_factory=list)  # 被@的人名（不含bot自己）
    is_at_bot: bool = False
    is_command: bool = False
    command: str = ""
    command_args: str = ""

    @property
    def needs_reply(self) -> bool:
        return self.is_at_bot and not self.is_command


def _extract_mentions(raw_content: str, content: str) -> list:
    """从 WeChat rawContent 提取 @mention 名字。"""
    mentions = []
    source = raw_content if ":\n" in raw_content else content
    if ":\n" in source:
        _, rest = source.split(":\n", 1)
        for part in rest.split(" "):
            part = part.strip()
            if part.startswith("@"):
                name = part[1:].strip()
                latin = re.match(r'([a-zA-Z][a-zA-Z0-9 ]*)', name)
                if latin:
                    name = latin.group(1).strip()
                else:
                    cjk = re.match(r'([一-鿿぀-ゟ가-힯]{2,4})', name)
                    if cjk:
                        name = cjk.group(1).strip()
                if name and name not in mentions:
                    mentions.append(name)
    return mentions


def parse(msg_data: dict, bot_names: list) -> Optional[ParsedMsg]:
    """将 WeFlow 原始消息解析为 ParsedMsg。私聊返回 None。"""
    talker = msg_data.get("talker", "") or msg_data.get("session_id", "")
    if "@chatroom" not in talker:
        return None  # 只处理群聊

    content = (msg_data.get("content", "") or "").strip()
    raw_content = msg_data.get("rawContent", "") or ""
    sender = msg_data.get("senderUsername", "") or ""

    # 提取显示名
    if ":\n" in raw_content:
        sender_name = raw_content.split(":\n")[0]
    else:
        sender_name = sender

    # 提取 mentions
    all_mentions = _extract_mentions(raw_content, content)
    mentioned_others = [m for m in all_mentions if m not in bot_names]

    # 检测 @bot
    is_at_bot = any(f"@{n}" in content for n in bot_names)

    # 清理文本
    text = content
    text = text.replace(' ', '')  # 去 WeChat mention 分隔符
    for name in bot_names:
        text = re.sub(rf'@{re.escape(name)}\s*', '', text).strip()

    # 命令检测
    is_cmd = False
    cmd = cmd_args = ""
    if is_at_bot and text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""
        is_cmd = True

    return ParsedMsg(
        room_id=talker,
        sender_wxid=sender,
        sender_name=sender_name,
        content=text,
        raw_mentions=mentioned_others,
        is_at_bot=is_at_bot,
        is_command=is_cmd,
        command=cmd,
        command_args=cmd_args,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/chatbot && python -m pytest tests/test_parse.py -v
```
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/parse.py tests/test_parse.py
git commit -m "feat: Parse stage — extract mentions, commands, strip separators"
```

---

### Task 6: Enrich Stage (Name Resolution + Memory Retrieval)

**Files:**
- Create: `src/enrich.py`
- Create: `tests/test_enrich.py`

**Interfaces:**
- Produces: `EnrichedCtx` dataclass
- Produces: `enrich(parsed: ParsedMsg, store: Store) -> EnrichedCtx`
- Consumes: `ParsedMsg` from Task 5, `Store` from Task 4

- [ ] **Step 1: Write the failing test**

```python
# tests/test_enrich.py
import pytest
from src.store import Store, ChatMsg
from src.parse import ParsedMsg
from src.enrich import enrich, EnrichedCtx


def test_enrich_resolves_mentions():
    store = Store()
    store.get_or_create_person("wxid_a", "子南")
    store.get_or_create_person("wxid_b", "贯一")
    store.get_group("123@chatroom")
    store.add_memory("123@chatroom", "子南喜欢人马片", keywords=["子南", "人马"])

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="@子南 你那个番号找到了吗",
        raw_mentions=["子南"], is_at_bot=True,
    )

    ctx = enrich(parsed, store)
    assert ctx is not None
    # 在场的人应该包含子南
    assert any("子南" in p["name"] for p in ctx.people.values())
    # 应该有相关记忆
    assert any("人马片" in m.text for m in ctx.related_memories)


def test_enrich_not_at_bot_returns_none():
    store = Store()
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="随便聊聊", raw_mentions=[], is_at_bot=False,
    )
    ctx = enrich(parsed, store)
    assert ctx is None  # 非@消息不返回上下文（但仍然记录了历史）


def test_enrich_scan_known_aliases_in_text():
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.add_alias("南哥")
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="南哥最近在干嘛",  # 没加@，但提到了外号
        raw_mentions=[], is_at_bot=True,
    )
    ctx = enrich(parsed, store)
    assert any("子南" in p["name"] or "南哥" in p["name"] for p in ctx.people.values())
```

- [ ] **Step 3: Implement**

```python
# src/enrich.py
"""上下文充实阶段 — 名字解析 + 记忆检索 + 别名扫描"""
from dataclasses import dataclass, field
from src.store import Store
from src.parse import ParsedMsg


@dataclass
class EnrichedCtx:
    parsed: ParsedMsg
    people: dict  # {wxid: {"name": str, "facts": str}}
    related_memories: list
    group_summary: str
    group_topic: str
    history: list
    mentionable_names: list  # LLM 可以用@名字的名单


def enrich(parsed: ParsedMsg, store: Store) -> EnrichedCtx | None:
    """充实上下文。非@消息返回 None（调用方仍需记录历史）。"""
    group = store.get_group(parsed.room_id)
    if not parsed.is_at_bot:
        return None

    people: dict = {}

    # 解析显式 @ 的人
    for name in parsed.raw_mentions:
        person, matched = store.resolve_name(name)
        if person and person.wxid != parsed.sender_wxid:
            people[person.wxid] = {
                "name": person.mention_name or matched or name,
                "facts": person.get_fact_strings(),
            }
        elif person is None and name:
            # 不认识的人，创建占位
            p = store.get_or_create_person(name, name)
            people[p.wxid] = {"name": name, "facts": ""}

    # 扫描正文中的已知别名
    for wxid, person in store._people.items():
        if wxid in people or wxid == parsed.sender_wxid:
            continue
        for alias in person.aliases:
            if alias and len(alias) >= 2 and alias in parsed.content:
                people[wxid] = {
                    "name": person.mention_name or alias,
                    "facts": person.get_fact_strings(),
                }
                break

    # 检索相关记忆
    keywords = list(parsed.raw_mentions) + list(people.keys())
    memories = store.search_memories(parsed.room_id, keywords, limit=3)

    # 可 mention 的名字列表
    mentionable = []
    for wxid, info in people.items():
        name = info["name"]
        if name and name != parsed.sender_name:
            mentionable.append(name)

    return EnrichedCtx(
        parsed=parsed,
        people=people,
        related_memories=memories,
        group_summary=group.context,
        group_topic=group.topic,
        history=store.get_history(parsed.room_id, limit=10),
        mentionable_names=mentionable,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/chatbot && python -m pytest tests/test_enrich.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/enrich.py tests/test_enrich.py
git commit -m "feat: Enrich stage — name resolution + memory retrieval + alias scanning"
```

---

### Task 7-12 概览（后续任务待展开）

由于篇幅，后续任务在此摘要列出，每个任务遵循相同的 TDD 结构（test → fail → implement → pass → commit）：

| Task | File | 核心功能 |
|------|------|---------|
| 7 | `src/prompt.py` | 四段式 prompt 组装：system + summary + history + current |
| 8 | `src/llm.py` | DeepSeek API 调用 + tool_use (search_web) + retry/fallback |
| 9 | `src/decode.py` | 回复解码：提取 @mentions, /remember, /context, 纠正信号 |
| 10 | `src/send.py` | UIA 内联 @mention 发送 |
| 11 | `src/pipeline.py` | 主循环：编排六个阶段 |
| 12 | `src/weflow.py` | WeFlow REST 客户端（从现有 weflow_client.py 迁移+精简） |
| 13 | `src/config.py` | YAML 配置加载 |
| 14 | `src/uia.py` | UIA 键盘模拟（从现有 uia_sender.py 迁移） |
| 15 | `main.py` | 入口点 |
| 16 | `src/migrate.py` | 旧数据迁移（users.json + group_memories.json → store.json） |
| 17 | — | 清理旧文件 + 集成测试 |

每个任务的详细步骤（test代码、实现代码、命令）见后续展开。

---

## 当前状态

- ✅ Task 1-6: 设计完成，含完整测试代码和实现代码
- ⏳ Task 7-17: 摘要已出，待展开详细步骤

**要继续展开剩余任务吗？还是先开始实现 Task 1-6？**
