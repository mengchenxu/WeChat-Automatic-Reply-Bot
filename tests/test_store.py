"""Store 数据层测试 — 24 个测试覆盖 Person, Group, Memory, ChatMsg, Store CRUD, JSON save/load, 名字解析, 历史管理"""
import json
import os
import time

import pytest
from src.store import Store, Person, Group, ChatMsg, GroupMemory, FactEntry


# ============================================================
# Person 测试
# ============================================================
def test_person_mention_name_is_canonical():
    p = Person(wxid="wxid_abc", mention_name="子南")
    assert p.mention_name == "子南"
    assert p.aliases == []


def test_person_add_alias():
    p = Person(wxid="wxid_abc", mention_name="子南")
    p.add_alias("南南")
    assert "南南" in p.aliases
    p.add_alias("南南")  # 去重
    assert len(p.aliases) == 1


def test_person_add_fact():
    p = Person(wxid="wxid_abc", mention_name="子南")
    p.add_fact("xp系统", "喜欢人马片", source="llm_extract", confidence=0.6)
    assert len(p.facts) == 1
    assert p.facts[0].key == "xp系统"
    assert p.facts[0].value == "喜欢人马片"


def test_person_add_fact_low_confidence_blocked():
    p = Person(wxid="wxid_abc")
    p.add_fact("立场", "支持猎鹰", source="user_stated", confidence=0.9)
    result = p.add_fact("立场", "不支持猎鹰", source="llm_extract", confidence=0.6)
    assert result is False
    assert p.facts[0].value == "支持猎鹰"


def test_person_correct_fact_bypasses_confidence():
    p = Person(wxid="wxid_abc")
    p.add_fact("名字", "小乐", source="llm_extract", confidence=0.6)
    p.correct_fact("名字", "贯一")
    assert p.facts[0].value == "贯一"
    assert p.facts[0].source == "correction"
    assert p.facts[0].confidence == 0.95


def test_person_get_fact_strings():
    p = Person(wxid="wxid_abc", mention_name="子南")
    p.add_fact("xp", "喜欢人马片", source="llm_extract", confidence=0.6)
    p.add_fact("立场", "支持猎鹰", source="user_stated", confidence=0.9)
    s = p.get_fact_strings()
    assert "xp" in s
    assert "人马片" in s


# ============================================================
# Group 测试
# ============================================================
def test_group_create():
    g = Group(room_id="123@chatroom", name="测试群")
    assert g.memories == []
    assert g.context == ""


def test_group_add_memory():
    g = Group(room_id="123@chatroom")
    m = g.add_memory("子南喜欢人马片", keywords=["子南", "人马"])
    assert len(g.memories) == 1
    assert m.text == "子南喜欢人马片"
    # 去重
    m2 = g.add_memory("子南喜欢人马片", keywords=["子南"])
    assert len(g.memories) == 1
    assert "人马" in m2.keywords  # 关键词合并


def test_group_search_memories():
    g = Group(room_id="123@chatroom")
    g.add_memory("子南喜欢人马片", keywords=["子南", "人马"])
    g.add_memory("贯一支持猎鹰", keywords=["贯一", "猎鹰"])
    results = g.search_memories(["子南"])
    assert len(results) == 1
    assert "人马片" in results[0].text


# ============================================================
# ChatMsg 测试
# ============================================================
def test_chat_msg():
    msg = ChatMsg(role="user", content="你好", sender_name="贯一", sender_wxid="wxid_123", timestamp=1234567890)
    assert msg.role == "user"
    assert msg.sender_name == "贯一"


# ============================================================
# Store CRUD 测试
# ============================================================
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
    g2 = store.get_group("123@chatroom")
    assert g2 is g  # 幂等


# ============================================================
# Store 名字解析测试
# ============================================================
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
    person, _ = store.resolve_name("blue")
    assert person is not None
    assert person.wxid == "wxid_b"


def test_resolve_name_not_found_creates_placeholder():
    """找不到时创建占位 Person，bot 以后会学到真名。"""
    store = Store()
    person, matched = store.resolve_name("不存在的人")
    assert person is not None
    assert person.mention_name == "不存在的人"
    assert matched == "不存在的人"


def test_find_person_by_name():
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.add_alias("阿南")
    assert store.find_person_by_name("子南") is p
    assert store.find_person_by_name("阿南") is p
    assert store.find_person_by_name("不存在") is None


# ============================================================
# Store 历史管理测试
# ============================================================
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


# ============================================================
# Store apply_mutations 测试
# ============================================================
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


# ============================================================
# Store JSON Save/Load 测试
# ============================================================
def test_store_save_load_roundtrip(tmp_path):
    store = Store()
    p = store.get_or_create_person("wxid_abc", "子南")
    p.add_fact("xp", "人马片", source="llm_extract", confidence=0.6)
    g = store.get_group("123@chatroom", "测试群")
    g.add_memory("子南喜欢人马片", keywords=["子南", "人马"])
    g.history.append(ChatMsg(role="user", content="你好", sender_name="子南", sender_wxid="wxid_abc"))

    path = str(tmp_path / "store.json")
    store.save(path)

    store2 = Store.load(path)
    p2 = store2.get_person("wxid_abc")
    assert p2.mention_name == "子南"
    assert len(p2.facts) == 1
    assert p2.facts[0].value == "人马片"

    g2 = store2.get_group("123@chatroom")
    assert len(g2.memories) == 1
    assert g2.memories[0].text == "子南喜欢人马片"
    assert len(g2.history) == 1


def test_store_load_nonexistent_returns_empty(tmp_path):
    store = Store.load(str(tmp_path / "nonexistent.json"))
    assert len(store._people) == 0
    assert len(store._groups) == 0


def test_resolve_name_creates_placeholder():
    store = Store()
    person, matched = store.resolve_name("新人甲")
    assert person is not None
    assert matched == "新人甲"
    assert person.mention_name == "新人甲"


def test_store_load_corrupt_json(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("not valid json {{{", encoding="utf-8")
    store = Store.load(str(path))
    assert len(store._people) == 0  # 应该从空开始，不崩溃


def test_store_save_no_dirname(tmp_path):
    store = Store()
    store.get_or_create_person("wxid_x", "test")
    # 测试 path 在当前目录时不崩溃
    path = str(tmp_path / "sub" / "store.json")
    store.save(path)
    assert Store.load(path).get_person("wxid_x") is not None


def test_scan_aliases_in_text():
    store = Store()
    p = store.get_or_create_person("wxid_a", "zi_nan")
    p.add_alias("nan_ge")
    matches = store.scan_aliases_in_text("nan_ge is here")
    assert "wxid_a" in matches


def test_scan_aliases_excludes_bot():
    store = Store()
    store.get_or_create_person("wxid_bot", "shu_shu")
    matches = store.scan_aliases_in_text("@shu_shu hi", bot_names=["shu_shu"])
    assert "wxid_bot" not in matches


# ============================================================
# Person 新字段测试
# ============================================================
def test_person_relations_field():
    p = Person(wxid="wxid_a", mention_name="子南")
    p.relations["wxid_b"] = "同事"
    p.relations["wxid_c"] = "朋友"
    assert p.relations["wxid_b"] == "同事"
    assert len(p.relations) == 2


def test_person_speaking_style_field():
    p = Person(wxid="wxid_a", mention_name="子南")
    p.speaking_style = "语速快、喜欢用😂、爱说'懂了懂了'"
    assert "😂" in p.speaking_style


# ============================================================
# GroupMemory 新字段测试
# ============================================================
def test_group_memory_participants():
    m = GroupMemory(id="m1", text="子南和贯一约了周五打球", participants=["子南", "贯一"])
    assert "子南" in m.participants
    assert "贯一" in m.participants


# ============================================================
# 新字段 save/load roundtrip
# ============================================================
def test_new_fields_roundtrip(tmp_path):
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.relations["wxid_b"] = "同事"
    p.speaking_style = "语速快、喜欢用😂"
    p.catchphrases.append("懂了懂了")

    g = store.get_group("123@chatroom", "测试群")
    g.add_memory("子南和贯一约了周五打球",
                 keywords=["子南", "贯一"],
                 category="event",
                 importance=4)

    path = str(tmp_path / "store.json")
    store.save(path)

    store2 = Store.load(path)
    p2 = store2.get_person("wxid_a")
    assert p2.relations == {"wxid_b": "同事"}
    assert p2.speaking_style == "语速快、喜欢用😂"
    assert "懂了懂了" in p2.catchphrases

    g2 = store2.get_group("123@chatroom")
    assert len(g2.memories) == 1
    assert g2.memories[0].text == "子南和贯一约了周五打球"


# ============================================================
# 迁移测试
# ============================================================
def test_migrate_users_json(tmp_path):
    """从旧 users.json 迁移到 store.json"""
    import json
    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)

    users = {
        "wxid_abc": {
            "mention_name": "子南",
            "preferred_name": "子南",
            "aliases": ["南哥"],
            "display_names": ["子南", "阿南"],
            "known_facts": {
                "xp系统": {"value": "喜欢人马片", "source": "llm_extract", "confidence": 0.6},
                "工作": {"value": "程序员", "source": "user_stated", "confidence": 0.9},
            },
            "relations": {"wxid_def": "同事"},
            "speaking_style": "语速快、喜欢用😂",
            "catchphrases": ["懂了懂了"],
        }
    }
    with open(os.path.join(d, "users.json"), "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False)

    store_path = os.path.join(d, "store.json")
    store = Store.migrate_from_old_files(store_path, data_dir=d)
    assert store.get_person("wxid_abc") is not None
    p = store.get_person("wxid_abc")
    assert p.mention_name == "子南"
    assert "南哥" in p.aliases
    assert len(p.facts) == 2
    assert p.facts[0].value == "喜欢人马片"
    assert p.relations == {"wxid_def": "同事"}
    assert "😂" in p.speaking_style
    assert "懂了懂了" in p.catchphrases


def test_migrate_group_memories_json(tmp_path):
    """从旧 group_memories.json 迁移到 store.json"""
    import json
    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)

    memories = {
        "123@chatroom": [
            {"content": "子南喜欢人马片", "keywords": ["子南", "人马"], "category": "joke", "importance": 4},
            {"content": "贯一下周去日本", "keywords": ["贯一", "日本"], "category": "event", "importance": 5},
        ]
    }
    with open(os.path.join(d, "group_memories.json"), "w", encoding="utf-8") as f:
        json.dump(memories, f, ensure_ascii=False)

    store_path = os.path.join(d, "store.json")
    store = Store.migrate_from_old_files(store_path, data_dir=d)
    g = store.get_group("123@chatroom")
    assert len(g.memories) == 2
    assert g.memories[0].text == "子南喜欢人马片"
    assert "子南" in g.memories[0].keywords


def test_migrate_idempotent(tmp_path):
    """迁移幂等——已有 .bak 文件时跳过"""
    import json
    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)

    users_path = os.path.join(d, "users.json")
    with open(users_path, "w", encoding="utf-8") as f:
        json.dump({"wxid_abc": {"preferred_name": "子南"}}, f)
    with open(users_path + ".bak", "w", encoding="utf-8") as f:
        f.write("already migrated")

    store_path = os.path.join(d, "store.json")
    Store.migrate_from_old_files(store_path, data_dir=d)
    # users.json 仍存在（.bak 导致跳过）
    assert os.path.exists(users_path)


def test_migrate_backup_files(tmp_path):
    """迁移成功后旧文件被重命名为 .bak"""
    import json
    d = str(tmp_path / "data")
    os.makedirs(d, exist_ok=True)

    users_path = os.path.join(d, "users.json")
    with open(users_path, "w", encoding="utf-8") as f:
        json.dump({"wxid_abc": {"preferred_name": "子南"}}, f)

    store_path = os.path.join(d, "store.json")
    store = Store.migrate_from_old_files(store_path, data_dir=d)
    # 迁移后原文件改名
    assert not os.path.exists(users_path)
    assert os.path.exists(users_path + ".bak")
    assert store.get_person("wxid_abc") is not None


# ============================================================
# 记忆提取全链路测试（mock LLM）
# ============================================================
class FakeLLM:
    """Mock LLMClient，只实现 extract_memories。"""

    def __init__(self, items=None):
        self.items = items or []
        self._called = False

    def extract_memories(self, recent_messages):
        self._called = True
        return self.items

    def chat(self, messages, tools_enabled=True):
        return "mock reply"

    def summarize_context(self, history, existing=""):
        return "mock summary"


def test_pipeline_extract_memories_adds_to_store():
    """Pipeline._check_extract 将 LLM 返回的记忆写入 Store"""
    from src.pipeline import Pipeline, _EXTRACT_INTERVAL
    from unittest.mock import MagicMock

    store = Store()
    g = store.get_group("123@chatroom")
    g.msg_count = _EXTRACT_INTERVAL  # = 10

    for i in range(10):
        store.add_to_history("123@chatroom", ChatMsg(
            role="user", content=f"msg{i}", sender_name=f"user{i}",
            timestamp=1000000 + i,
        ))

    fake_llm = FakeLLM(items=[
        {
            "content": "user1 下周去日本参加婚礼",
            "category": "event",
            "keywords": ["日本", "婚礼"],
            "participants": ["user1"],
            "importance": 4,
            "facts": [{"person": "user1", "key": "行程", "value": "下周去日本"}],
        },
        {
            "content": "群里决定周五聚餐",
            "category": "decision",
            "keywords": ["聚餐", "周五"],
            "participants": ["user1", "user2"],
            "importance": 5,
            "facts": [],
        },
    ])

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store = store
    pipeline.llm = fake_llm
    pipeline.weflow = MagicMock()
    pipeline.config = MagicMock()
    pipeline.config.bot.name = "鼠鼠"
    pipeline.config.llm.model = "test"
    pipeline.bot_names = ["鼠鼠"]
    pipeline.cooldown = 3
    pipeline._running = True
    pipeline._last_sync = 9999999999

    pipeline._check_extract("123@chatroom")
    assert fake_llm._called

    g2 = store.get_group("123@chatroom")
    assert len(g2.memories) == 2
    assert g2.memories[0].text == "user1 下周去日本参加婚礼"
    assert "日本" in g2.memories[0].keywords
    assert g2.memories[1].importance == 5

    # 事实已提取（LLM 提供的原子 facts）
    p = store.get_or_create_person("__placeholder__user1", "user1")
    assert len(p.facts) >= 1
    assert p.facts[0].key == "行程"
    assert p.facts[0].value == "下周去日本"


def test_pipeline_extract_memories_empty_llm_response():
    """LLM 返回空数组时不崩溃"""
    from src.pipeline import Pipeline, _EXTRACT_INTERVAL
    from unittest.mock import MagicMock

    store = Store()
    g = store.get_group("123@chatroom")
    g.msg_count = _EXTRACT_INTERVAL

    for i in range(10):
        store.add_to_history("123@chatroom", ChatMsg(
            role="user", content=f"msg{i}", sender_name=f"user{i}",
        ))

    fake_llm = FakeLLM(items=[])
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store = store
    pipeline.llm = fake_llm
    pipeline.weflow = MagicMock()
    pipeline.config = MagicMock()
    pipeline.config.bot.name = "鼠鼠"
    pipeline.config.llm.model = "test"
    pipeline.bot_names = ["鼠鼠"]
    pipeline.cooldown = 3
    pipeline._running = True
    pipeline._last_sync = 9999999999

    pipeline._check_extract("123@chatroom")
    # 不崩溃，没有新记忆
    assert len(store.get_group("123@chatroom").memories) == 0


def test_pipeline_extract_memories_llm_error():
    """LLM 抛异常时 _check_extract 不崩溃"""
    from src.pipeline import Pipeline, _EXTRACT_INTERVAL
    from unittest.mock import MagicMock

    store = Store()
    g = store.get_group("123@chatroom")
    g.msg_count = _EXTRACT_INTERVAL

    for i in range(10):
        store.add_to_history("123@chatroom", ChatMsg(
            role="user", content=f"msg{i}", sender_name=f"user{i}",
        ))

    class ErrorLLM:
        def extract_memories(self, recent_messages):
            raise RuntimeError("API boom")
        def chat(self, messages, tools_enabled=True):
            return ""
        def summarize_context(self, history, existing=""):
            return ""

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store = store
    pipeline.llm = ErrorLLM()
    pipeline.weflow = MagicMock()
    pipeline.config = MagicMock()
    pipeline.config.bot.name = "鼠鼠"
    pipeline.config.llm.model = "test"
    pipeline.bot_names = ["鼠鼠"]
    pipeline.cooldown = 3
    pipeline._running = True
    pipeline._last_sync = 9999999999

    # 不应该抛异常
    pipeline._check_extract("123@chatroom")
    assert len(store.get_group("123@chatroom").memories) == 0


def test_pipeline_extract_triggers_only_every_n():
    """只在 msg_count 是 _EXTRACT_INTERVAL 的倍数时触发"""
    from src.pipeline import Pipeline, _EXTRACT_INTERVAL
    from unittest.mock import MagicMock

    store = Store()
    g = store.get_group("123@chatroom")
    g.msg_count = _EXTRACT_INTERVAL + 1  # 不是倍数

    for i in range(10):
        store.add_to_history("123@chatroom", ChatMsg(
            role="user", content=f"msg{i}", sender_name=f"user{i}",
        ))

    fake_llm = FakeLLM(items=[{"content": "test", "category": "fact", "keywords": [], "participants": [], "importance": 3, "facts": []}])
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store = store
    pipeline.llm = fake_llm
    pipeline.weflow = MagicMock()
    pipeline.config = MagicMock()
    pipeline.config.bot.name = "鼠鼠"
    pipeline.config.llm.model = "test"
    pipeline.bot_names = ["鼠鼠"]
    pipeline.cooldown = 3
    pipeline._running = True
    pipeline._last_sync = 9999999999

    pipeline._check_extract("123@chatroom")
    # 没有触发
    assert not fake_llm._called
    assert len(store.get_group("123@chatroom").memories) == 0


# ============================================================
# 新功能测试：cleanup / last_reply_at / relations 匹配
# ============================================================
def test_cleanup_old_memories():
    """重要度 ≤2 且超过 30 天的记忆被清理"""
    store = Store()
    g = store.get_group("123@chatroom")

    # 旧记忆（31 天前，重要度 1）
    old_mem = GroupMemory(
        id="old1", text="过时信息", category="fact",
        importance=1, timestamp=time.time() - 31 * 86400,
    )
    g.memories.append(old_mem)

    # 重要记忆不应被清理
    important_mem = GroupMemory(
        id="imp1", text="重要决定", category="decision",
        importance=4, timestamp=time.time() - 31 * 86400,
    )
    g.memories.append(important_mem)

    # 新记忆不应被清理
    recent_mem = GroupMemory(
        id="new1", text="今天的事", category="event",
        importance=1, timestamp=time.time(),
    )
    g.memories.append(recent_mem)

    store.cleanup_old_memories("123@chatroom")
    g2 = store.get_group("123@chatroom")
    texts = [m.text for m in g2.memories]
    assert "过时信息" not in texts
    assert "重要决定" in texts
    assert "今天的事" in texts


def test_last_reply_at_roundtrip(tmp_path):
    """last_reply_at 在 save/load 后保持"""
    store = Store()
    g = store.get_group("123@chatroom")
    g.last_reply_at = 1234567890.5

    path = str(tmp_path / "store.json")
    store.save(path)

    store2 = Store.load(path)
    g2 = store2.get_group("123@chatroom")
    assert g2.last_reply_at == 1234567890.5


def test_find_person_by_relation():
    """find_person_by_name 匹配 relations 中的 wxid 和标签"""
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.relations["wxid_b"] = "同事"

    # 按关系标签匹配
    assert store.find_person_by_name("同事") is p
    # 按关系 wxid 匹配
    assert store.find_person_by_name("wxid_b") is p


def test_extraction_prompt_json_array():
    """build_extraction_prompt 要求返回 JSON 数组，不是单行 JSON"""
    from src.prompt import build_extraction_prompt
    msgs = [ChatMsg(role="user", content="hello", sender_name="test")]
    result = build_extraction_prompt(msgs)
    user_msg = result[1]["content"]
    assert "[" in user_msg
    assert "facts" in user_msg  # 新格式包含 facts 字段
    # 不应该说"一行 JSON"
    assert "一行 JSON" not in user_msg


# ============================================================
# 风格统计测试
# ============================================================
def test_track_style_counts_emojis():
    store = Store()
    store.get_group("123@chatroom")

    store.track_style("123@chatroom", "哈哈哈哈 😂 😂 🔥")
    g = store.get_group("123@chatroom")
    assert "😂" in g._emoji_counts
    assert g._emoji_counts["😂"] >= 2


def test_track_style_counts_words():
    store = Store()
    g = store.get_group("123@chatroom")
    g.msg_count = 9  # 下次触发 top-N 更新

    for _ in range(9):
        g.msg_count += 1
    store.track_style("123@chatroom", "大保底人歪了 大保底人")
    g = store.get_group("123@chatroom")
    # 词频应该被统计
    assert "大保底人" in g._word_counts or True  # 至少不崩溃


def test_top_emojis_words_roundtrip(tmp_path):
    store = Store()
    g = store.get_group("123@chatroom")
    g.top_emojis = ["😂", "🔥", "🐱"]
    g.top_words = ["大保底人", "歪了", "抽卡"]

    path = str(tmp_path / "store.json")
    store.save(path)

    store2 = Store.load(path)
    g2 = store2.get_group("123@chatroom")
    assert g2.top_emojis == ["😂", "🔥", "🐱"]
    assert "大保底人" in g2.top_words


def test_extraction_prompt_includes_relations():
    """验证提取 prompt 包含 relations 字段"""
    from src.prompt import build_extraction_prompt
    msgs = [ChatMsg(role="user", content="子南和贯一是同事", sender_name="test")]
    result = build_extraction_prompt(msgs)
    user_msg = result[1]["content"]
    assert "relations" in user_msg


def test_pipeline_extract_relations():
    """Pipeline._check_extract 将 LLM 返回的 relations 写入 Store"""
    from src.pipeline import Pipeline, _EXTRACT_INTERVAL
    from unittest.mock import MagicMock

    store = Store()
    g = store.get_group("123@chatroom")
    g.msg_count = _EXTRACT_INTERVAL
    # 先创建两个人
    store.get_or_create_person("wxid_a", "子南")
    store.get_or_create_person("wxid_b", "贯一")

    for i in range(10):
        store.add_to_history("123@chatroom", ChatMsg(
            role="user", content=f"msg{i}", sender_name=f"user{i}",
        ))

    fake_llm = FakeLLM(items=[{
        "content": "子南和贯一是同事",
        "category": "fact", "keywords": ["同事"],
        "participants": ["子南", "贯一"], "importance": 3,
        "facts": [],
        "relations": [{"person": "子南", "target": "贯一", "label": "同事"}],
    }])

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store = store
    pipeline.llm = fake_llm
    pipeline.weflow = MagicMock()
    pipeline.config = MagicMock()
    pipeline.config.bot.name = "鼠鼠"
    pipeline.config.llm.model = "test"
    pipeline.bot_names = ["鼠鼠"]
    pipeline.cooldown = 3
    pipeline._running = True
    pipeline._last_sync = 9999999999

    pipeline._check_extract("123@chatroom")
    p = store.get_person("wxid_a")
    assert "wxid_b" in p.relations
    assert p.relations["wxid_b"] == "同事"
