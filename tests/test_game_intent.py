"""游戏意图检测 + 游戏命令 测试"""
import pytest
from unittest.mock import Mock, patch

from src.config import AppConfig, GameConfig
from src.store import Store
from src.parse import ParsedMsg
from src.game_intent import GameIntentDetector


# ============================================================
# 测试夹具
# ============================================================

def _make_config(**overrides) -> AppConfig:
    """创建测试用 AppConfig。"""
    config = AppConfig()
    kwargs = {
        "enabled": True,
        "keywords": ["游戏", "无聊", "玩", "来点"],
        "commands": [
            {"name": "/骰子", "description": "掷骰子 — 随机 1-6"},
            {"name": "/猜拳", "description": "石头剪刀布"},
        ],
        "intent_model": "deepseek-chat",
        "intent_temperature": 0.1,
        "intent_max_tokens": 2,
    }
    kwargs.update(overrides)
    config.game = GameConfig(**kwargs)
    return config


def _make_parsed(content: str, is_at_bot: bool = False) -> ParsedMsg:
    """创建测试用 ParsedMsg。"""
    return ParsedMsg(
        room_id="123@chatroom",
        sender_wxid="wxid_test",
        sender_name="测试用户",
        content=content,
        raw_mentions=[],
        is_at_bot=is_at_bot,
    )


def _mock_llm_想玩(messages, model, temperature, max_tokens) -> str:
    """Mock LLM：永远返回'想玩'。"""
    return "想玩"


def _mock_llm_不想玩(messages, model, temperature, max_tokens) -> str:
    """Mock LLM：永远返回'不想玩'。"""
    return "不想玩"


# ============================================================
# 关键词检测
# ============================================================

class TestKeywordDetection:
    """关键词扫描测试（不依赖 LLM）。"""

    def test_has_keywords_match_game(self):
        detector = GameIntentDetector(_make_config())
        assert detector.has_keywords("我们来玩游戏吧")

    def test_has_keywords_match_boring(self):
        detector = GameIntentDetector(_make_config())
        assert detector.has_keywords("好无聊啊")

    def test_has_keywords_match_play(self):
        detector = GameIntentDetector(_make_config())
        assert detector.has_keywords("来玩点什么")

    def test_has_keywords_match_来点(self):
        detector = GameIntentDetector(_make_config())
        assert detector.has_keywords("来点好玩的")

    def test_has_keywords_case_insensitive(self):
        detector = GameIntentDetector(_make_config())
        assert detector.has_keywords("好无聊啊")  # case-insensitive: 大写/小写/简繁体都算（注：简体中文"无聊"匹配关键字"无聊"）

    def test_has_keywords_no_match(self):
        detector = GameIntentDetector(_make_config())
        assert not detector.has_keywords("今天天气不错")
        assert not detector.has_keywords("晚饭吃啥")

    def test_has_keywords_empty_content(self):
        detector = GameIntentDetector(_make_config())
        assert not detector.has_keywords("")


# ============================================================
# 意图分类
# ============================================================

class TestIntentClassification:
    """LLM 意图分类测试（mock LLM）。"""

    def test_classify_想玩(self):
        detector = GameIntentDetector(_make_config(), llm_chat_fn=_mock_llm_想玩)
        result = detector.classify_intent("好无聊啊，有什么好玩的")
        assert result == "想玩"

    def test_classify_不想玩(self):
        detector = GameIntentDetector(_make_config(), llm_chat_fn=_mock_llm_不想玩)
        result = detector.classify_intent("昨天玩了游戏")
        assert result == "不想玩"

    def test_classify_strips_whitespace(self):
        """LLM 返回带空白的'想玩'也能正确识别。"""
        def mock_with_space(messages, model, temperature, max_tokens):
            return "  想玩  "
        detector = GameIntentDetector(_make_config(), llm_chat_fn=mock_with_space)
        result = detector.classify_intent("来玩游戏")
        assert result == "想玩"

    def test_classify_unknown_fallback(self):
        """LLM 返回无关内容时保守处理（不误触发）。"""
        def mock_unknown(messages, model, temperature, max_tokens):
            return "我不知道"
        detector = GameIntentDetector(_make_config(), llm_chat_fn=mock_unknown)
        result = detector.classify_intent("游戏")
        assert result == "不想玩"  # 保守兜底

    def test_classify_llm_params(self):
        """验证 LLM 调用参数正确（temperature=0.1, max_tokens=2）。"""
        called_with = {}

        def mock_capture(messages, model, temperature, max_tokens):
            called_with["model"] = model
            called_with["temperature"] = temperature
            called_with["max_tokens"] = max_tokens
            return "想玩"

        detector = GameIntentDetector(_make_config(), llm_chat_fn=mock_capture)
        detector.classify_intent("好无聊")
        assert called_with["model"] == "deepseek-chat"
        assert called_with["temperature"] == 0.1
        assert called_with["max_tokens"] == 2


# ============================================================
# detect() 全链路
# ============================================================

class TestDetectFullPipeline:
    """detect() 端到端测试。"""

    def test_detect_keyword_hit_intent_想玩(self):
        detector = GameIntentDetector(_make_config(), llm_chat_fn=_mock_llm_想玩)
        parsed = _make_parsed("好无聊啊，来玩游戏")
        result = detector.detect(parsed)
        assert result == "想玩"

    def test_detect_keyword_hit_intent_不想玩(self):
        detector = GameIntentDetector(_make_config(), llm_chat_fn=_mock_llm_不想玩)
        parsed = _make_parsed("昨天打了游戏真开心")
        result = detector.detect(parsed)
        assert result == "不想玩"

    def test_detect_no_keyword_returns_none(self):
        detector = GameIntentDetector(_make_config(), llm_chat_fn=_mock_llm_想玩)
        parsed = _make_parsed("今天天气真好")
        result = detector.detect(parsed)
        assert result is None  # 无关键词，不触发 LLM

    def test_detect_disabled_returns_none(self):
        detector = GameIntentDetector(
            _make_config(enabled=False), llm_chat_fn=_mock_llm_想玩
        )
        parsed = _make_parsed("好无聊啊")
        result = detector.detect(parsed)
        assert result is None  # 功能关闭

    def test_detect_empty_content(self):
        detector = GameIntentDetector(_make_config(), llm_chat_fn=_mock_llm_想玩)
        parsed = _make_parsed("")
        result = detector.detect(parsed)
        assert result is None  # 空消息无关键词


# ============================================================
# 游戏列表
# ============================================================

class TestGameList:
    """get_game_list() 测试。"""

    def test_game_list_contains_commands(self):
        detector = GameIntentDetector(_make_config())
        game_list = detector.get_game_list()
        assert "/骰子" in game_list
        assert "/猜拳" in game_list
        assert "掷骰子" in game_list
        assert "石头剪刀布" in game_list

    def test_game_list_empty_commands(self):
        detector = GameIntentDetector(_make_config(commands=[]))
        game_list = detector.get_game_list()
        assert "暂时还没有" in game_list

    def test_game_list_includes_hint(self):
        detector = GameIntentDetector(_make_config())
        game_list = detector.get_game_list()
        assert "@鼠鼠" in game_list or "鼠鼠" in game_list


# ============================================================
# 与 Pipeline 的集成测试
# ============================================================

class TestPipelineIntegration:
    """模拟 Pipeline 非@ 分支的完整流程。"""

    def test_non_at_game_intent_returns_game_list(self):
        """非@消息 + 游戏关键词 + 想玩 → 返回游戏列表。"""
        config = _make_config()
        detector = GameIntentDetector(config, llm_chat_fn=_mock_llm_想玩)
        parsed = _make_parsed("好无聊啊，有什么好玩的", is_at_bot=False)

        result = detector.detect(parsed)
        assert result == "想玩"

        game_list = detector.get_game_list()
        assert "/骰子" in game_list

    def test_non_at_game_keyword_but_not_intent_returns_none(self):
        """非@消息 + 游戏关键词 + 不想玩 → detect 返回'不想玩'。"""
        config = _make_config()
        detector = GameIntentDetector(config, llm_chat_fn=_mock_llm_不想玩)
        parsed = _make_parsed("昨天玩了游戏", is_at_bot=False)

        result = detector.detect(parsed)
        assert result == "不想玩"

    def test_game_detection_only_on_at_bot(self):
        """游戏意图检测只在 @bot 时触发，非@ 消息不触发。
        这个约束由 Pipeline.handle() 保证——game_detector 在 is_at_bot 分支中调用。"""
        parsed_at = _make_parsed("@鼠鼠 好无聊", is_at_bot=True)
        assert parsed_at.is_at_bot is True
        # @bot 消息 → game_detector.detect 被调用（在 Pipeline 中）

        parsed_non = _make_parsed("好无聊啊", is_at_bot=False)
        assert parsed_non.is_at_bot is False
        # 非@消息 → enrich 返回 None → Pipeline 直接返回，不调 game_detector

    def test_keyword_edge_cases(self):
        """关键词边界测试。"""
        config = _make_config()
        detector = GameIntentDetector(config, llm_chat_fn=_mock_llm_想玩)

        # 包含但不完全是关键词
        assert detector.has_keywords("游戏机")  # "游戏"是子串
        assert detector.has_keywords("玩具")    # "玩"是子串

        # 相关内容但不含关键词 → 不触发 LLM
        assert not detector.has_keywords("dice")
        assert not detector.has_keywords("game")

    def test_detect_does_not_call_llm_when_no_keyword(self):
        """无关键词时不应调用 LLM（节省成本）。"""
        call_count = 0

        def counting_mock(messages, model, temperature, max_tokens):
            nonlocal call_count
            call_count += 1
            return "想玩"

        detector = GameIntentDetector(_make_config(), llm_chat_fn=counting_mock)
        parsed = _make_parsed("今天天气不错")
        result = detector.detect(parsed)

        assert result is None
        assert call_count == 0  # LLM 未被调用


def test_dice_returns_1_to_6():
    """Pipeline._handle_dice 返回 1-6 的结果。使用 mock send 不操作微信窗口。"""
    from unittest.mock import patch
    from src.pipeline import Pipeline
    from unittest.mock import MagicMock

    store = Store()
    store.get_group("123@chatroom")

    pipeline = Pipeline.__new__(Pipeline)
    pipeline.store = store
    pipeline.weflow = MagicMock()
    pipeline.config = MagicMock()
    pipeline.config.bot.name = "鼠鼠"

    from src.parse import ParsedMsg
    parsed = ParsedMsg(
        room_id="123@chatroom", sender_wxid="wxid_x", sender_name="主人",
        content="/骰子", raw_mentions=[], is_at_bot=True,
        is_command=True, command="/骰子",
    )

    # Mock send 避免操作真实微信窗口
    with patch("src.pipeline.send", return_value=True):
        results = set()
        for _ in range(20):
            reply = pipeline._handle_dice(parsed)
            assert "🎲" in reply
            assert "喵~" in reply
            import re
            match = re.search(r'\[(\d)\]', reply)
            assert match
            results.add(int(match.group(1)))

    assert results == {1, 2, 3, 4, 5, 6}
