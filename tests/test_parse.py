"""Parse 阶段测试"""
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
    assert "鼠鼠" not in result.content


def test_parse_at_bot_and_other():
    msg = make_msg("@鼠鼠 @子南  你认识他吗", sender="wxid_a")
    result = parse(msg, bot_names=["鼠鼠"])
    assert result.is_at_bot is True
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
    assert "贯一" in result.raw_mentions
    assert "B L U E" in result.raw_mentions
    assert "鼠鼠" not in result.raw_mentions


def test_parse_private_message_ignored():
    msg = {
        "content": "你好",
        "rawContent": "sender:\n你好",
        "senderUsername": "wxid_a",
        "localId": "msg_001",
        "talker": "wxid_a",
    }
    result = parse(msg, bot_names=["鼠鼠"])
    assert result is None


def test_parse_real_weflow_message():
    """用真实 WeFlowMessage 对象测试——防止 dict/object 不兼容。"""
    from src.weflow_client import WeFlowMessage
    raw = {
        "content": "@鼠鼠 @子南 你好",
        "rawContent": "xiaoleilei169816:\n@鼠鼠 @子南  你好",
        "senderUsername": "xiaoleilei169816",
        "localId": "msg_real_001",
        "createTime": 1234567890,
        "talker": "43495935852@chatroom",
    }
    wm = WeFlowMessage(raw, session_type="group", session_name="test")
    wm.session_id = "43495935852@chatroom"

    result = parse(wm, bot_names=["鼠鼠"])
    assert result is not None
    assert result.is_at_bot is True
    assert "子南" in result.raw_mentions
    assert "鼠鼠" not in result.content


def test_parse_ignores_bot_own_message():
    """bot 自己的消息应该被忽略，防止自我回复无限循环。"""
    msg = {
        "talker": "123@chatroom",
        "content": "🎲 人家摇出了 [3] 喵~",
        "rawContent": "鼠鼠:\n🎲 人家摇出了 [3] 喵~",
        "senderUsername": "鼠鼠",
    }
    result = parse(msg, bot_names=["鼠鼠"])
    assert result is None
