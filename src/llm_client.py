"""
LLM 接入层 — OpenAI 兼容 API 调用、Prompt 管理、异常兜底。
"""
import logging
from typing import List

from openai import OpenAI

from src.config_loader import AppConfig
from src.bot_core import ChatMessage

logger = logging.getLogger(__name__)


class LLMClient:
    """封装 OpenAI 兼容 API，支持 DeepSeek / GPT / 豆包 等。"""

    def __init__(self, config: AppConfig):
        llm = config.llm
        self.system_prompt = config.bot.system_prompt
        self.max_tokens = llm.max_tokens
        self.temperature = llm.temperature
        self.model = llm.model

        self.client = OpenAI(
            api_key=llm.api_key,
            base_url=llm.base_url,
        )

    def chat(self, history: List[ChatMessage]) -> str:
        """
        调用 LLM 进行多轮对话。
        history: 该群的对话历史（ChatMessage 列表），不含 system prompt。
        返回 LLM 回复文本；异常时返回兜底文案。
        """
        # 构建 messages：system prompt + 历史
        messages = [{"role": "system", "content": self.system_prompt}]
        for m in history:
            messages.append({"role": m.role, "content": m.content})

        logger.debug("LLM 请求: %d 条消息", len(messages))

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            reply = resp.choices[0].message.content
            logger.debug("LLM 回复: %s", reply[:100])
            return self._sanitize(reply)

        except Exception as e:
            logger.exception("LLM 调用失败")
            return self._fallback_reply(e)

    # ----------------------------------------------------------------
    # 回复预处理
    # ----------------------------------------------------------------
    def _sanitize(self, text: str) -> str:
        """回复后处理：去空、截断过长内容、处理特殊字符。"""
        if not text:
            return "（对方暂时没有回应）"
        text = text.strip()
        # 微信消息有长度限制，截断到 2000 字符
        if len(text) > 2000:
            text = text[:1997] + "..."
        return text

    # ----------------------------------------------------------------
    # 异常兜底
    # ----------------------------------------------------------------
    def _fallback_reply(self, error: Exception) -> str:
        """LLM 异常时的兜底回复。"""
        err_msg = str(error)
        if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
            return "🤔 思考超时了，稍等片刻再问我吧～"
        if "rate" in err_msg.lower() or "429" in err_msg:
            return "⏳ 问得太快了，让我喘口气再回答你～"
        if "401" in err_msg or "403" in err_msg:
            return "🔑 API 密钥配置有误，请检查配置。"
        return "😵 大脑短路了，稍后再试～"
