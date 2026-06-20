"""
LLM 接入层 — OpenAI 兼容 API 调用、Tool Use、Prompt 管理、异常兜底。
"""
import json
import logging
from typing import List

from openai import OpenAI

from src.config_loader import AppConfig
from src.bot_core import ChatMessage

logger = logging.getLogger(__name__)

# search_web 工具定义（当搜索功能可用时启用）
_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "搜索网络了解不懂的网络用语、梗、流行语、新闻事件。"
            "当你遇到不确定含义的网络用语、流行语、梗、新闻热点时使用此工具。"
            "搜索结果会告诉你这个梗/词的含义和出处，你就可以自然地接住这个梗了。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如 '电子布洛芬 是什么梗' 或 'xx 含义 网络用语'",
                }
            },
            "required": ["query"],
        },
    },
}


class LLMClient:
    """封装 OpenAI 兼容 API，支持 DeepSeek / GPT / 豆包 + Tool Use。"""

    def __init__(self, config: AppConfig):
        llm = config.llm
        self.system_prompt = config.bot.system_prompt
        self.max_tokens = llm.max_tokens
        self.temperature = llm.temperature
        self.model = llm.model
        self.enable_search = getattr(config.bot, 'enable_search', True)

        self.client = OpenAI(
            api_key=llm.api_key,
            base_url=llm.base_url,
        )

    def chat(self, history: List[ChatMessage], tools_enabled: bool = True) -> str:
        """
        调用 LLM 进行多轮对话，支持 tool use（search_web）。
        最多 2 轮 tool call 循环。

        history: 该群的对话历史（ChatMessage 列表），不含 system prompt。
        返回 LLM 回复文本；异常时返回兜底文案。
        """
        # 构建 messages：system prompt + 历史
        messages = [{"role": "system", "content": self.system_prompt}]
        for m in history:
            messages.append({"role": m.role, "content": m.content})

        tools = [_SEARCH_TOOL] if (tools_enabled and self.enable_search) else None
        logger.debug("LLM 请求: %d 条消息, tools=%s", len(messages), "on" if tools else "off")

        try:
            # 第一轮调用
            kwargs = dict(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            resp = self.client.chat.completions.create(**kwargs)
            choice = resp.choices[0]

            # 检查是否有 tool call
            if tools and choice.message.tool_calls:
                tool_calls = choice.message.tool_calls

                # 追加 assistant 消息（含 tool_calls）
                messages.append({
                    "role": "assistant",
                    "content": choice.message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                })

                # 执行每个 tool call
                for tc in tool_calls:
                    if tc.function.name == "search_web":
                        try:
                            args = json.loads(tc.function.arguments)
                            query = args.get("query", "")
                        except (json.JSONDecodeError, KeyError):
                            query = tc.function.arguments.strip()

                        logger.info("Tool call: search_web(%s)", query[:60])
                        from src.web_search import search_web, search_format_for_llm
                        results = search_web(query)
                        result_text = search_format_for_llm(results)

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_text,
                        })

                # 第二轮调用（生成最终回复）
                resp2 = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                reply = resp2.choices[0].message.content
            else:
                reply = choice.message.content

            logger.debug("LLM 回复: %s", reply[:100] if reply else "(空)")
            return self._sanitize(reply)

        except Exception as e:
            logger.exception("LLM 调用失败")
            return self._fallback_reply(e)

    # ----------------------------------------------------------------
    # 上下文摘要
    # ----------------------------------------------------------------
    def summarize_context(self, history: list, existing_context: str = "") -> str:
        """
        让 LLM 从对话历史中提取群聊上下文摘要。
        用于定期更新 GroupSession.group_context。
        """
        if not history:
            return ""

        history_text = []
        for m in history[-20:]:
            role = "用户" if m.role == "user" else "助手"
            name = getattr(m, 'sender_name', '') or ''
            tag = f"{role}({name})" if name else role
            history_text.append(f"[{tag}]: {m.content[:200]}")

        existing = f"之前的群聊背景:\n{existing_context}\n\n" if existing_context else ""

        prompt = f"""请阅读以下群聊对话，总结当前群聊的背景信息。用 2-4 句话概括：

{existing}最近对话:
{chr(10).join(history_text)}

请提炼并返回（纯文本，不要 markdown 格式）：
1. 群成员特征（谁是谁，有什么特点/偏好）
2. 当前讨论的主要话题
3. 任何值得记住的共识或决定

注意：
- 如果之前已有背景，请基于它更新/补充，不要丢失旧信息
- 每条信息控制在 1-2 句
- 不要编造信息
- 只需返回总结文字，无需前缀"""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个群聊记录员，负责简洁地总结群聊背景信息。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
                temperature=0.3,
            )
            summary = resp.choices[0].message.content
            logger.debug("上下文摘要: %s", summary[:120] if summary else "(空)")
            return self._sanitize(summary) if summary else ""

        except Exception as e:
            logger.exception("上下文摘要生成失败")
            return ""

    # ----------------------------------------------------------------
    # 话题摘要（轻量，用于工作记忆更新）
    # ----------------------------------------------------------------
    def summarize_topic(self, recent_messages: list, old_summary: str = "") -> tuple[str, list[str]]:
        """
        增量式更新当前话题摘要。
        返回 (话题摘要, 关键词列表)。
        """
        if not recent_messages:
            return old_summary, []

        lines = []
        for m in recent_messages[-10:]:
            name = getattr(m, 'sender_name', '') or '未知'
            role = "用户" if m.role == "user" else "助手"
            lines.append(f"[{role}({name})]: {m.content[:150]}")

        old_info = f"旧话题摘要: {old_summary}\n\n" if old_summary else ""

        prompt = f"""基于以下群聊消息，更新当前话题信息。

{old_info}最近消息:
{chr(10).join(lines)}

请返回一句话话题摘要 + 3-5个关键词（JSON 格式）：
{{"summary": "群内在聊xxx，涉及xxx", "keywords": ["关键词1", "关键词2", "关键词3"]}}

只返回 JSON，不要其他文字。"""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你负责跟踪群聊话题。只返回 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=200,
                temperature=0.3,
            )
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            data = json.loads(text)
            return data.get("summary", old_summary), data.get("keywords", [])
        except Exception:
            logger.exception("话题摘要更新失败")
            return old_summary, []

    # ----------------------------------------------------------------
    # 回复预处理
    # ----------------------------------------------------------------
    def _sanitize(self, text: str) -> str:
        if not text:
            return "（对方暂时没有回应）"
        text = text.strip()
        if len(text) > 2000:
            text = text[:1997] + "..."
        return text

    # ----------------------------------------------------------------
    # 异常兜底
    # ----------------------------------------------------------------
    def _fallback_reply(self, error: Exception) -> str:
        err_msg = str(error)
        if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
            return "🤔 思考超时了，稍等片刻再问我吧～"
        if "rate" in err_msg.lower() or "429" in err_msg:
            return "⏳ 问得太快了，让我喘口气再回答你～"
        if "401" in err_msg or "403" in err_msg:
            return "🔑 API 密钥配置有误，请检查配置。"
        return "😵 大脑短路了，稍后再试～"
