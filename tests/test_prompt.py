"""Prompt 组装测试"""
import pytest
from src.prompt import build_prompt
from src.enrich import EnrichedCtx
from src.parse import ParsedMsg
from src.store import Store, ChatMsg, GroupMemory


def test_build_prompt_four_sections():
    store = Store()
    store.get_group("123@chatroom")
    store.add_to_history("123@chatroom", ChatMsg(role="user", content="你好", sender_name="贯一"))
    store.add_to_history("123@chatroom", ChatMsg(role="assistant", content="你好啊", sender_name="鼠鼠"))

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name="贯一",
        content="猎鹰能拿major吗", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(
        parsed=parsed,
        people={},
        related_memories=[],
        group_summary="群内在聊CS比赛",
        history=store.get_history("123@chatroom"),
        mentionable_names=[],
    )

    system_prompt = "你是鼠鼠，孙吧14级。"
    messages = build_prompt(enriched, system_prompt)

    # 第一条是 system
    assert messages[0]["role"] == "system"
    assert "鼠鼠" in messages[0]["content"]

    # 最后一条是 user（当前消息）
    assert messages[-1]["role"] == "user"
    assert "猎鹰能拿major吗" in messages[-1]["content"]


def test_build_prompt_includes_summary():
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name="贯一",
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(
        parsed=parsed,
        group_summary="群内在聊抽象兄弟团",
        history=[],
    )
    messages = build_prompt(enriched, "你是鼠鼠")
    user_msg = messages[-1]["content"]
    assert "群内在聊抽象兄弟团" in user_msg


def test_build_prompt_includes_history():
    store = Store()
    store.get_group("123@chatroom")
    store.add_to_history("123@chatroom", ChatMsg(role="user", content="msg1", sender_name="a"))
    store.add_to_history("123@chatroom", ChatMsg(role="assistant", content="reply1", sender_name="鼠鼠"))

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name="a",
        content="msg2", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(
        parsed=parsed,
        history=store.get_history("123@chatroom"),
    )
    messages = build_prompt(enriched, "你是鼠鼠")
    user_msg = messages[-1]["content"]
    assert "msg1" in user_msg
    assert "reply1" in user_msg


def test_build_prompt_truncates_history_to_10():
    store = Store()
    store.get_group("123@chatroom")
    for i in range(15):
        store.add_to_history("123@chatroom", ChatMsg(role="user", content=f"msg{i}", sender_name="a"))

    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name="a",
        content="latest", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(
        parsed=parsed, history=store.get_history("123@chatroom", limit=15),
    )
    messages = build_prompt(enriched, "你是鼠鼠")
    user_msg = messages[-1]["content"]
    # 应该只有最近 10 条
    assert "msg14" in user_msg
    assert "msg5" in user_msg
    assert "msg4" not in user_msg  # 超出 10 条


def test_mixianshan_prompt_identity():
    """验证新 system_prompt 包含米线山人设关键元素。"""
    from src.config import load_config
    config = load_config()
    sp = config.bot.system_prompt
    assert "米线山" in sp
    assert "2-5 句话" in sp
    assert "严禁拆成多段" in sp
    assert "损友" in sp
    assert "串子" in sp
    # 旧 prompt 元素必须清理（新 prompt 中作为"不要用"的说明可以出现）
    assert "孙笑川" not in sp
    assert "孙吧" not in sp
    assert "嘴臭" not in sp
    assert "骂完就跑" not in sp


def test_build_prompt_none_sender_name():
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name=None,
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(parsed=parsed, history=[])
    messages = build_prompt(enriched, "你是鼠鼠")
    user_msg = messages[-1]["content"]
    assert "@未知" in user_msg  # fallback
