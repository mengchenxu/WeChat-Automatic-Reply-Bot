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


def test_build_prompt_includes_mentionable():
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_a", sender_name="贯一",
        content="test", raw_mentions=[], is_at_bot=True,
    )
    enriched = EnrichedCtx(
        parsed=parsed,
        mentionable_names=["子南", "咚咚"],
        history=[],
    )
    messages = build_prompt(enriched, "你是鼠鼠")
    user_msg = messages[-1]["content"]
    assert "子南" in user_msg
    assert "咚咚" in user_msg
    assert "@名字" in user_msg  # 格式指令
