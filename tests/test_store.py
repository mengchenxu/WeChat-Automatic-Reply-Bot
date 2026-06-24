"""Store 数据层测试 — 24 个测试覆盖 Person, Group, Memory, ChatMsg, Store CRUD, JSON save/load, 名字解析, 历史管理"""
import pytest
import json
import os
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


def test_store_load_nonexistent_returns_empty():
    store = Store.load("data/nonexistent.json")
    assert len(store._people) == 0
    assert len(store._groups) == 0
