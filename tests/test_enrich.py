"""Enrich 阶段测试"""
import pytest
from src.store import Store
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
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    assert ctx is not None
    assert any("子南" in p["name"] for p in ctx.people.values())
    assert any("人马片" in m.text for m in ctx.related_memories)


def test_enrich_not_at_bot_returns_none():
    store = Store()
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="随便聊聊", raw_mentions=[], is_at_bot=False,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    assert ctx is None


def test_enrich_scan_known_aliases_in_text():
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.add_alias("南哥")
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="南哥最近在干嘛", raw_mentions=[], is_at_bot=True,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    assert any("子南" in p["name"] or "南哥" in p["name"] for p in ctx.people.values())


def test_enrich_unknown_mention_creates_placeholder():
    store = Store()
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="@陌生人 你是谁", raw_mentions=["陌生人"], is_at_bot=True,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    assert any("陌生人" in p["name"] for p in ctx.people.values())
    # 占位 Person 应该已被创建
    assert store.get_person("__placeholder__陌生人") is not None


def test_enrich_excludes_bot_from_people():
    store = Store()
    p = store.get_or_create_person("wxid_bot", "鼠鼠")
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name="贯一",
        content="@鼠鼠 你在吗", raw_mentions=[], is_at_bot=True,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    # 鼠鼠不应该出现在"在场的人"里
    assert not any("鼠鼠" in p["name"] for p in ctx.people.values())


def test_enrich_mentionable_names():
    store = Store()
    store.get_or_create_person("wxid_a", "子南")
    store.get_or_create_person("wxid_b", "贯一")
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="@子南 @贯一 都来", raw_mentions=["子南", "贯一"], is_at_bot=True,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    assert "子南" in ctx.mentionable_names
    assert "贯一" in ctx.mentionable_names


def test_enrich_injects_relations():
    """Enrich 阶段注入 Person.relations"""
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.relations["wxid_b"] = "同事"
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="@子南 你在吗", raw_mentions=["子南"], is_at_bot=True,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    people_list = list(ctx.people.values())
    assert any("wxid_b:同事" in p.get("relations", "") for p in people_list)


def test_enrich_injects_speaking_style():
    """Enrich 阶段注入 Person.speaking_style"""
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.speaking_style = "语速快、喜欢用😂"
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="@子南 在不在", raw_mentions=["子南"], is_at_bot=True,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    people_list = list(ctx.people.values())
    assert any("😂" in p.get("style", "") for p in people_list)


def test_enrich_injects_group_style():
    """Enrich 阶段注入 group_style（表情+高频词+成员风格）"""
    store = Store()
    p = store.get_or_create_person("wxid_a", "子南")
    p.speaking_style = "语速快"
    g = store.get_group("123@chatroom")
    g.top_emojis = ["😂", "🔥"]
    g.top_words = ["大保底人", "歪了"]

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_c", sender_name="测试",
        content="@子南 hi", raw_mentions=["子南"], is_at_bot=True,
    )
    ctx = enrich(parsed, store, bot_names=["鼠鼠"])
    assert "😂" in ctx.group_style
    assert "大保底人" in ctx.group_style
    # 成员风格也应该在
    assert "子南" in ctx.group_style
