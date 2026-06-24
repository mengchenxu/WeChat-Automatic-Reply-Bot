"""Decode 阶段测试"""
import pytest
from src.decode import decode
from src.enrich import EnrichedCtx
from src.parse import ParsedMsg
from src.store import Store


def test_decode_extracts_at_mentions():
    store = Store()
    store.get_or_create_person("wxid_a", "子南")
    store.get_or_create_person("wxid_b", "贯一")
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_x", sender_name="咚咚",
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(parsed=parsed, mentionable_names=["子南", "贯一"], history=[])

    raw = "不是 @子南 你也太抽象了 比 @贯一 还离谱"
    result = decode(raw, enriched, store)
    # 应该提取两个 mention
    assert "子南" in result.at_mentions or "贯一" in result.at_mentions
    # 不应该包含发送者自己
    assert "咚咚" not in result.at_mentions


def test_decode_strips_at_sender():
    store = Store()
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_x", sender_name="咚咚",
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(parsed=parsed, history=[])

    raw = "@咚咚 你说的对"
    result = decode(raw, enriched, store)
    # 开头 @发送者应被去掉
    assert not result.clean_text.startswith("@咚咚")


def test_decode_extracts_remember():
    store = Store()
    store.get_or_create_person("wxid_a", "子南")
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_x", sender_name="咚咚",
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(parsed=parsed, history=[])

    raw = "好的\n/remember @子南 xp系统: 从人马升级到鹿了"
    result = decode(raw, enriched, store)
    assert len(result.mutations.get("add_facts", {})) >= 1


def test_decode_extracts_context_update():
    store = Store()
    store.get_group("123@chatroom")
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_x", sender_name="咚咚",
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(parsed=parsed, history=[])

    raw = "好的\n/context 群内在聊抽象兄弟团，子南和咚咚互甩锅"
    result = decode(raw, enriched, store)
    assert "123@chatroom" in result.mutations.get("update_summary", {})


def test_decode_never_leaks_wxid_in_reply():
    """bug: LLM写了@wxid_xxx，正则匹配不完整导致wxid碎片留在正文。"""
    store = Store()
    p = store.get_or_create_person("wxid_o1nbmvbfmktu22", "子南")
    p.mention_name = "子南"  # mention_name 已正确设置
    store.get_group("123@chatroom")

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_x", sender_name="贯一",
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(parsed=parsed, history=[])

    # 模拟 LLM 可能写出 wxid（因为上下文里看到了）
    raw = "@wxid_o1nbmvbfmktu22 你那个番号找到了吗"
    result = decode(raw, enriched, store)
    # 关键是：_o1nbmvbfmktu22 不能泄露到正文
    assert "_o1nbmvbfmktu22" not in result.clean_text
    assert "wxid_o1nbmvbfmktu22" not in result.clean_text
    # 应该正确识别并替换为用户可读名
    assert "子南" in result.at_mentions or result.clean_text == "你那个番号找到了吗"


def test_decode_correction_signal():
    store = Store()
    p = store.get_or_create_person("wxid_a", "贯一")
    p.add_fact("名字", "小乐", source="llm_extract", confidence=0.6)
    store.get_group("123@chatroom")

    # 模拟: 贯一纠正 bot 说"我不叫小乐"
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name="贯一",
        content="我不叫小乐", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(parsed=parsed, history=[])

    raw = "我不叫小乐，我叫贯一"
    result = decode(raw, enriched, store)
    # 应该触发纠正
    corrections = result.mutations.get("correct_facts", {})
    assert "wxid_a" in corrections or len(corrections) > 0
