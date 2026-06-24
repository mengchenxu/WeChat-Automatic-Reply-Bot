"""LLM 调用阶段 — DeepSeek API + tool use (search_web) + retry/fallback"""
import json
import logging
from typing import Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "搜索网络了解不懂的网络用语、梗、流行语。遇到不确定含义的词时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    },
}


class LLMClient:
    """封装 OpenAI 兼容 API（DeepSeek）。支持 tool use + fallback。"""

    def __init__(self, config):
        llm = config.llm
        self.model = llm.model
        self.max_tokens = llm.max_tokens
        self.temperature = llm.temperature

        self.client = OpenAI(
            api_key=llm.api_key,
            base_url=llm.base_url,
        )

    def chat(self, messages: List[Dict[str, str]], tools_enabled: bool = True) -> str:
        """调用 LLM，支持 search_web tool use（最多 1 轮 tool call）。返回回复文本。"""
        tools = [_SEARCH_TOOL] if tools_enabled else None

        try:
            kwargs = dict(
                model=self.model, messages=messages,
                max_tokens=self.max_tokens, temperature=self.temperature,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            resp = self.client.chat.completions.create(**kwargs)
            choice = resp.choices[0]

            # Tool call 处理
            if tools and choice.message.tool_calls:
                tool_calls = choice.message.tool_calls

                messages.append({
                    "role": "assistant",
                    "content": choice.message.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in tool_calls
                    ],
                })

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

                resp2 = self.client.chat.completions.create(
                    model=self.model, messages=messages,
                    max_tokens=self.max_tokens, temperature=self.temperature,
                )
                reply = resp2.choices[0].message.content
            else:
                reply = choice.message.content

            return _sanitize(reply)

        except Exception as e:
            logger.exception("LLM 调用失败")
            return _fallback_reply(e)


    def summarize_context(self, history: list, existing_context: str = "") -> str:
        """让 LLM 从对话历史中提取群聊摘要。"""
        if not history:
            return ""

        lines = []
        for m in history[-15:]:
            role = "用户" if (hasattr(m, 'role') and m.role == "user") else "助手"
            name = getattr(m, 'sender_name', '') or ''
            tag = f"{role}({name})" if name else role
            lines.append(f"[{tag}]: {m.content[:200]}")

        existing = f"之前的群聊背景:\n{existing_context}\n\n" if existing_context else ""

        prompt = f"""请阅读以下群聊对话，总结当前群聊的背景信息。用 2-4 句话概括：

{existing}最近对话:
{"\n".join(lines)}

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
            return _sanitize(summary) if summary else ""
        except Exception:
            logger.exception("上下文摘要生成失败")
            return ""


def _sanitize(text: Optional[str]) -> str:
    """清理 LLM 回复：截断超长、处理空值。"""
    if not text:
        return "（对方暂时没有回应）"
    text = text.strip()
    if len(text) > 2000:
        text = text[:1997] + "..."
    return text


def _fallback_reply(error: Exception) -> str:
    """异常兜底文案，不暴露技术细节。"""
    err_msg = str(error)
    if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
        return "🤔 思考超时了，稍等片刻再问我吧～"
    if "rate" in err_msg.lower() or "429" in err_msg:
        return "⏳ 问得太快了，让我喘口气再回答你～"
    if "401" in err_msg or "403" in err_msg:
        return "🔑 API 密钥配置有误，请检查配置。"
    return "😵 大脑短路了，稍后再试～"
