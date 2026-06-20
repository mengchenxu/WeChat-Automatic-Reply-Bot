"""
Web 搜索封装 — DuckDuckGo，免费无 API key，支持中文。
"""
import logging

logger = logging.getLogger(__name__)

try:
    from duckduckgo_search import DDGS
    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False
    logger.warning("duckduckgo_search 未安装，搜索功能不可用")


def search_web(query: str, max_results: int = 5) -> list[dict]:
    """
    搜索网络。
    返回: [{"title": "...", "snippet": "...", "url": "..."}, ...]
    异常/无结果时返回空列表。
    """
    if not _HAS_DDGS:
        logger.warning("DDGS 不可用，跳过搜索: %s", query)
        return []

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return [
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                }
                for r in results
            ]
    except Exception:
        logger.exception("搜索失败: %s", query)
        return []


def search_format_for_llm(results: list[dict]) -> str:
    """将搜索结果格式化为 LLM 可读文本。"""
    if not results:
        return "（未找到相关信息）"
    lines = []
    for i, r in enumerate(results[:5], 1):
        lines.append(f"{i}. {r['title']}\n   {r['snippet'][:200]}")
    return "\n".join(lines)
