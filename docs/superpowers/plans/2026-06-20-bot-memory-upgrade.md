# 群聊机器人记忆升级 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 建立三层记忆体系（工作/情景/语义）+ tool use 热梗搜索，让机器人对话连贯、跨会话回忆、主动关联、自然接梗。

**Architecture:** 新增 GroupMemoryStore 管理群级情景记忆，增强 UserMemoryStore 自动提取用户事实，LLMClient 增加 tool use 循环支持 search_web，重写 context_builder 实现三层记忆检索与上下文组装。

**Tech Stack:** Python 3.x, deepseek-chat API (OpenAI 兼容), DuckDuckGo Search, dataclasses + JSON 持久化

## Global Constraints

- 保持现有文件结构，新增文件放入 `src/`，数据文件放入 `data/`
- JSON 文件持久化，UTF-8 编码
- 所有 LLM 调用通过 `LLMClient` 统一管理
- 回复仍通过 UIA 模拟键盘发送，不做改动
- 只响应群聊中 @bot 的消息，保持被动触发模式

## File Map

| 文件 | 职责 | 改动 |
|------|------|------|
| `src/group_memory.py` | 情景记忆：存储、检索、清理、LLM 提取 | 新建 |
| `src/web_search.py` | DuckDuckGo 搜索封装 | 新建 |
| `src/llm_client.py` | LLM 调用 + tool use 循环 | 重写 |
| `src/user_memory.py` | 用户档案：增强 relations + 事实去重 | 修改 |
| `src/bot_core.py` | GroupSession 增强：话题摘要 + 关键词 | 修改 |
| `src/context_builder.py` | 上下文组装：记忆检索 + 事实提取 + 梗参考 | 重写 |
| `config/config.yaml` | system prompt 重写 + 搜索开关 | 修改 |
| `src/config_loader.py` | BotConfig 添加 enable_search | 修改 |
| `main.py` | 集成 GroupMemoryStore | 修改 |
| `requirements.txt` | 添加 duckduckgo_search | 修改 |

---

### Task 1: 情景记忆存储 (GroupMemoryStore)

**Files:**
- Create: `src/group_memory.py`
- Test: 手动验证（持久化模块，无复杂逻辑，通过后续集成测试覆盖）

**Interfaces:**
- Produces: `GroupMemory` dataclass, `GroupMemoryStore` class
  - `GroupMemoryStore(data_dir: str)` — 构造函数，自动加载
  - `.add_memory(room_id, content, category, participants, keywords, importance) -> GroupMemory`
  - `.search(room_id, query_keywords, limit=3) -> list[GroupMemory]`
  - `.consolidate(room_id, messages: list, llm_client: LLMClient) -> list[GroupMemory]` — LLM 提取新记忆
  - `.forget_old(room_id, max_age_days=30)` — 清理旧记忆
  - `.save()` / `._load()` — 持久化

- [ ] **Step 1: 创建 `src/group_memory.py`**

```python
"""
群情景记忆 — 跨会话记住群内值得回顾的事件、决定、趣事。
持久化到 data/group_memories.json。
"""
import json
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GroupMemory:
    """一条群情景记忆"""
    id: str
    room_id: str
    content: str               # 记忆内容（1-3 句话）
    category: str               # event | decision | fact | joke | topic_change
    participants: list = field(default_factory=list)  # 涉及的显示名
    keywords: list = field(default_factory=list)       # 检索关键词
    timestamp: float = 0.0
    importance: int = 3        # 1-5，越高越重要


class GroupMemoryStore:
    """群情景记忆存储。持久化到 data/group_memories.json。"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.file_path = os.path.join(data_dir, "group_memories.json")
        self._memories: dict[str, list[GroupMemory]] = {}  # room_id -> [memories]
        os.makedirs(data_dir, exist_ok=True)
        self._load()

    # ----------------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------------
    def _load(self):
        if not os.path.exists(self.file_path):
            logger.info("群记忆文件不存在，从空开始: %s", self.file_path)
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for room_id, mems in data.items():
                self._memories[room_id] = [
                    GroupMemory(
                        id=m["id"],
                        room_id=m["room_id"],
                        content=m["content"],
                        category=m.get("category", "fact"),
                        participants=m.get("participants", []),
                        keywords=m.get("keywords", []),
                        timestamp=m.get("timestamp", 0.0),
                        importance=m.get("importance", 3),
                    )
                    for m in mems
                ]
            total = sum(len(v) for v in self._memories.values())
            logger.info("已加载 %d 条群记忆 (覆盖 %d 个群)", total, len(self._memories))
        except Exception:
            logger.exception("加载群记忆失败，从空开始")

    def save(self):
        try:
            data = {}
            for room_id, mems in self._memories.items():
                data[room_id] = [asdict(m) for m in mems]
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("保存群记忆失败")

    # ----------------------------------------------------------------
    # 记忆操作
    # ----------------------------------------------------------------
    def _make_id(self, room_id: str, content: str, timestamp: float) -> str:
        raw = f"{room_id}:{content}:{timestamp}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def add_memory(
        self,
        room_id: str,
        content: str,
        category: str = "fact",
        participants: list = None,
        keywords: list = None,
        importance: int = 3,
    ) -> GroupMemory:
        """添加一条情景记忆。自动去重（内容完全相同的跳过）。"""
        if room_id not in self._memories:
            self._memories[room_id] = []

        # 去重：检查是否已有相同内容
        for existing in self._memories[room_id]:
            if existing.content.strip() == content.strip():
                # 更新关键词和时间戳
                existing.keywords = list(set(existing.keywords + (keywords or [])))
                existing.timestamp = time.time()
                existing.importance = max(existing.importance, importance)
                logger.debug("记忆已存在，更新关键词: %s", content[:40])
                self.save()
                return existing

        mem = GroupMemory(
            id=self._make_id(room_id, content, time.time()),
            room_id=room_id,
            content=content.strip(),
            category=category,
            participants=participants or [],
            keywords=keywords or [],
            timestamp=time.time(),
            importance=importance,
        )
        self._memories[room_id].append(mem)
        logger.info("新记忆 [%s]: %s (%s)", room_id[:20], content[:60], category)
        self.save()
        return mem

    def search(self, room_id: str, query_keywords: list, limit: int = 3) -> list[GroupMemory]:
        """
        关键词匹配 + 时间衰减 检索相关记忆。
        返回按相关性得分降序排列的 top-N 记忆。
        """
        memories = self._memories.get(room_id, [])
        if not memories or not query_keywords:
            return []

        now = time.time()
        scored = []
        for mem in memories:
            # 关键词匹配度
            mem_kws = set(k.lower() for k in mem.keywords)
            query_kws = set(k.lower() for k in query_keywords)
            matches = len(mem_kws & query_kws)
            if matches == 0:
                continue
            # 时间衰减：每过一天乘以 0.95
            days_old = (now - mem.timestamp) / 86400
            time_decay = 0.95 ** days_old
            # 重要度加权
            score = matches * time_decay * (mem.importance / 3.0)
            scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:limit]]

    def forget_old(self, room_id: str, max_age_days: int = 30):
        """清理超过 max_age_days 天且重要度 <= 2 的记忆。"""
        if room_id not in self._memories:
            return
        now = time.time()
        cutoff = now - max_age_days * 86400
        before = len(self._memories[room_id])
        self._memories[room_id] = [
            m for m in self._memories[room_id]
            if not (m.timestamp < cutoff and m.importance <= 2)
        ]
        removed = before - len(self._memories[room_id])
        if removed:
            logger.info("清理 %s 条旧记忆 (群 %s)", removed, room_id[:20])
            self.save()

    def consolidate(
        self, room_id: str, recent_messages: list, llm_client
    ) -> list[GroupMemory]:
        """
        让 LLM 从最近消息中提取值得记住的情景记忆。
        返回新增的记忆列表。
        """
        if not recent_messages:
            return []

        # 构建提取 prompt
        lines = []
        for m in recent_messages[-15:]:
            name = getattr(m, 'sender_name', '') or '未知'
            role = "用户" if m.role == "user" else "助手"
            lines.append(f"[{role}({name})]: {m.content[:150]}")

        prompt = f"""阅读以下群聊消息，提取值得记住的内容（最多3条）。

消息:
{chr(10).join(lines)}

判断标准：
- 群成员的明确表态、偏好、计划（如"我下周去日本"）
- 群的共识或决定（如"大家都觉得去公园比较好"）
- 有趣的群内梗/笑点
- 重要的话题转折

对每条值得记住的内容，返回一行 JSON（不要其他文字）：
{{"content": "...", "category": "event|decision|fact|joke|topic_change", "keywords": ["k1","k2"], "participants": ["名字1"], "importance": 1-5}}

如果没有值得记住的，返回空列表 []
只返回 JSON 数组，不要 markdown 代码块。"""

        try:
            resp = llm_client.client.chat.completions.create(
                model=llm_client.model,
                messages=[
                    {"role": "system", "content": "你是一个群聊记录员。只返回 JSON 数组，不要其他文字。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
                temperature=0.3,
            )
            text = resp.choices[0].message.content.strip()
            # 清理可能的 markdown 代码块标记
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            items = json.loads(text)
            if not isinstance(items, list):
                items = []
        except Exception:
            logger.exception("记忆提取 LLM 调用失败")
            return []

        new_mems = []
        for item in items[:3]:  # 最多 3 条
            try:
                mem = self.add_memory(
                    room_id=room_id,
                    content=item.get("content", ""),
                    category=item.get("category", "fact"),
                    participants=item.get("participants", []),
                    keywords=item.get("keywords", []),
                    importance=min(5, max(1, item.get("importance", 3))),
                )
                new_mems.append(mem)
            except Exception:
                continue

        if new_mems:
            logger.info("提取 %d 条新记忆: 群 %s", len(new_mems), room_id[:20])
            # 清理旧记忆
            self.forget_old(room_id)

        return new_mems

    @property
    def memory_count(self) -> int:
        return sum(len(v) for v in self._memories.values())
```

- [ ] **Step 2: 快速验证导入**

```bash
cd D:/chatbot && python -c "from src.group_memory import GroupMemoryStore; s = GroupMemoryStore(); print('OK:', s.memory_count, 'memories')"
```

预期输出: `OK: 0 memories`

- [ ] **Step 3: Commit**

```bash
git add src/group_memory.py
git commit -m "feat: add group episodic memory store (GroupMemoryStore)"
```

---

### Task 2: Web 搜索

**Files:**
- Create: `src/web_search.py`
- Test: 手动验证

**Interfaces:**
- Produces: `search_web(query: str, max_results: int = 5) -> list[dict]`
  - 返回 `[{"title": str, "snippet": str, "url": str}, ...]`

- [ ] **Step 1: 安装依赖**

```bash
cd D:/chatbot && pip install duckduckgo_search
```

- [ ] **Step 2: 创建 `src/web_search.py`**

```python
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
```

- [ ] **Step 3: 验证**

```bash
cd D:/chatbot && python -c "from src.web_search import search_web; r = search_web('电子布洛芬 是什么梗'); print(len(r), 'results'); print(r[0]['title'] if r else 'no results')"
```

预期: 返回若干搜索结果

- [ ] **Step 4: Commit**

```bash
git add src/web_search.py
git commit -m "feat: add web search via DuckDuckGo"
```

---

### Task 3: LLMClient Tool Use

**Files:**
- Modify: `src/llm_client.py`（重写 `chat()` 方法支持 tool use 循环）
- Consumes: `search_web`, `search_format_for_llm` from Task 2

**Interfaces:**
- Consumes: `src.web_search.search_web`, `src.web_search.search_format_for_llm`
- Produces: `LLMClient.chat(history, tools_enabled=True) -> str`
  - 行为变更：当 `tools_enabled=True` 时，自动处理 tool call 循环

- [ ] **Step 1: 读取当前 `src/llm_client.py`**

文件已在上下文中，当前有 `chat()` 和 `summarize_context()` 两个方法。

- [ ] **Step 2: 重写 `src/llm_client.py`**

完整替换文件内容：

```python
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
```

- [ ] **Step 3: 验证**

```bash
cd D:/chatbot && python -c "from src.llm_client import LLMClient; print('Import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/llm_client.py
git commit -m "feat: add tool use loop (search_web) and topic summarization to LLMClient"
```

---

### Task 4: 用户记忆增强

**Files:**
- Modify: `src/user_memory.py` — 增强 `UserProfile`，新增 `relations` 字段和事实去重合并

**Interfaces:**
- Consumes: 无
- Produces: 增强的 `UserProfile` (新增 `relations: dict`), `UserMemoryStore.merge_fact()` 方法

- [ ] **Step 1: 读取当前 `UserProfile`**

已有字段：`wxid, display_names, preferred_name, first_seen, last_seen, message_count, known_facts, topics, notes`

- [ ] **Step 2: 修改 `UserProfile`**

在 `src/user_memory.py` 中修改 `UserProfile` 类：

```python
# 在 known_facts 后面添加 relations 字段
@dataclass
class UserProfile:
    """群成员档案"""
    wxid: str
    display_names: List[str] = field(default_factory=list)
    preferred_name: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    message_count: int = 0
    known_facts: Dict[str, str] = field(default_factory=dict)  # {"职业": "程序员", "喜欢": "猫"}
    relations: Dict[str, str] = field(default_factory=dict)     # {"wxid_xxx": "同事", "wxid_yyy": "朋友"}
    topics: List[str] = field(default_factory=list)
    notes: str = ""

    # ... 现有方法保持不变 ...
```

- [ ] **Step 3: 在 `UserMemoryStore` 中添加 `merge_fact` 和 `add_relation` 方法**

```python
def merge_fact(self, wxid: str, key: str, value: str) -> bool:
    """
    合并事实：如果已存在相似 key，更新而非覆盖。
    返回 True 表示有变更。
    """
    profile = self.get_or_create(wxid)
    # 检查是否已存在完全相同的事实
    if key in profile.known_facts and profile.known_facts[key] == value:
        return False
    # 检查是否有相似 key（如 "喜欢" vs "爱好"）
    similar_keys = {
        "喜欢": ["爱好", "偏好"],
        "爱好": ["喜欢", "偏好"],
        "职业": ["工作", "岗位"],
        "工作": ["职业", "岗位"],
    }
    for existing_key, existing_value in profile.known_facts.items():
        if existing_value == value:
            return False  # 值相同，key 不同也无所谓
        # 相似 key 合并
        related = similar_keys.get(key, [])
        if existing_key in related:
            profile.known_facts[key] = value
            del profile.known_facts[existing_key]
            self.save()
            return True

    profile.set_fact(key, value)
    self.save()
    return True

def add_relation(self, wxid: str, target_wxid: str, relation: str):
    """记录两个用户之间的关系。"""
    profile = self.get_or_create(wxid)
    profile.relations[target_wxid] = relation
    self.save()
```

- [ ] **Step 4: 更新 `get_context_summary` 以包含关系信息**

```python
def get_context_summary(self) -> str:
    """生成 LLM 可用的用户摘要"""
    parts = []
    if self.preferred_name:
        parts.append(f"名字: {self.preferred_name}")
    if self.known_facts:
        facts = ", ".join(f"{k}={v}" for k, v in self.known_facts.items())
        parts.append(f"已知: {facts}")
    if self.relations:
        rel_text = ", ".join(f"与{rid}关系={rel}" for rid, rel in self.relations.items())
        parts.append(f"关系: {rel_text}")
    if self.topics:
        parts.append(f"常聊: {', '.join(self.topics[-5:])}")
    if self.notes:
        parts.append(f"备注: {self.notes}")
    if not parts:
        return ""
    return " | ".join(parts)
```

- [ ] **Step 5: 更新 `load()` 和 `save()` 方法以处理 `relations` 字段**

在 `load()` 中：
```python
profile = UserProfile(
    wxid=wxid,
    display_names=d.get("display_names", []),
    preferred_name=d.get("preferred_name", ""),
    first_seen=d.get("first_seen", 0.0),
    last_seen=d.get("last_seen", 0.0),
    message_count=d.get("message_count", 0),
    known_facts=d.get("known_facts", {}),
    relations=d.get("relations", {}),  # 新增
    topics=d.get("topics", []),
    notes=d.get("notes", ""),
)
```

在 `save()` 中：
```python
data[wxid] = {
    "wxid": profile.wxid,
    "display_names": profile.display_names,
    "preferred_name": profile.preferred_name,
    "first_seen": profile.first_seen,
    "last_seen": profile.last_seen,
    "message_count": profile.message_count,
    "known_facts": profile.known_facts,
    "relations": profile.relations,  # 新增
    "topics": profile.topics,
    "notes": profile.notes,
}
```

- [ ] **Step 6: 验证**

```bash
cd D:/chatbot && python -c "from src.user_memory import UserMemoryStore; u = UserMemoryStore(); u.merge_fact('test_user', '喜欢', '猫'); p = u.get('test_user'); print(p.get_context_summary())"
```

- [ ] **Step 7: Commit**

```bash
git add src/user_memory.py
git commit -m "feat: add relations tracking and fact dedup to UserMemoryStore"
```

---

### Task 5: GroupSession 工作记忆增强

**Files:**
- Modify: `src/bot_core.py` — 增强 `GroupSession` dataclass

- [ ] **Step 1: 修改 `GroupSession` dataclass**

将 `src/bot_core.py` 中的 `GroupSession` 改为：

```python
@dataclass
class GroupSession:
    group_id: str
    history: deque = field(default_factory=lambda: deque(maxlen=20))  # 从 30 减少到 20
    last_reply_at: float = 0.0
    active_users: Set[str] = field(default_factory=set)
    group_context: str = ""
    message_count: int = 0
    # 新增：工作记忆字段
    topic_summary: str = ""               # 当前话题摘要
    topic_keywords: list = field(default_factory=list)  # 话题关键词
    message_since_summary: int = 0        # 距上次话题摘要的消息数
    message_since_memory: int = 0         # 距上次记忆提取的消息数
```

- [ ] **Step 2: 更新 `GroupSession` 的初始化**

在 `_get_session()` 方法中，`max_history` 从 `max_history_rounds * 2`（原来是30）改为固定20：

```python
def _get_session(self, roomid: str) -> GroupSession:
    if roomid not in self._sessions:
        self._sessions[roomid] = GroupSession(
            group_id=roomid,
            history=deque(maxlen=20),
        )
    return self._sessions[roomid]
```

- [ ] **Step 3: 在 `handle()` 中增加计数器**

在 `handle()` 方法中，`session.message_count += 1` 之后添加：

```python
session.message_since_summary += 1
session.message_since_memory += 1
```

- [ ] **Step 4: 添加触发判断方法**

```python
def should_update_topic(self, roomid: str) -> bool:
    """是否应该触发话题摘要更新（每 10 条消息）。"""
    session = self._get_session(roomid)
    return session.message_since_summary >= 10

def should_extract_memory(self, roomid: str) -> bool:
    """是否应该触发情景记忆提取（每 15 条消息）。"""
    session = self._get_session(roomid)
    return session.message_since_memory >= 15

def reset_summary_counter(self, roomid: str):
    session = self._get_session(roomid)
    session.message_since_summary = 0

def reset_memory_counter(self, roomid: str):
    session = self._get_session(roomid)
    session.message_since_memory = 0
```

- [ ] **Step 5: 验证**

```bash
cd D:/chatbot && python -c "from src.bot_core import GroupSession; s = GroupSession(group_id='test'); print('topic:', s.topic_summary, 'keywords:', s.topic_keywords)"
```

- [ ] **Step 6: Commit**

```bash
git add src/bot_core.py
git commit -m "feat: enhance GroupSession with topic tracking and memory extraction triggers"
```

---

### Task 6: 上下文组装重写

**Files:**
- Rewrite: `src/context_builder.py`

**Interfaces:**
- Consumes: `GroupMemoryStore` (Task 1), `UserMemoryStore` (Task 4), `GroupSession` (Task 5)
- Produces: `build_llm_context()` — 组装完整上下文
- Produces: `auto_extract_facts()` — 自动从 LLM 回复中提取用户事实
- Produces: `extract_facts_from_reply()` — 保留并增强
- Produces: `extract_context_from_reply()` — 保留不变

- [ ] **Step 1: 重写 `src/context_builder.py`**

```python
"""
上下文构建器 — 三层记忆检索 + 用户档案 + 热梗参考 组装 LLM 上下文。
"""
import logging
import re

from src.user_memory import UserMemoryStore
from src.bot_core import GroupSession
from src.group_memory import GroupMemoryStore
from src.weflow_client import WeFlowMessage

logger = logging.getLogger(__name__)


def build_llm_context(
    msg: WeFlowMessage,
    session: GroupSession,
    user_memory: UserMemoryStore,
    group_memory: GroupMemoryStore,
    bot_nicknames: list,
    search_result: str = "",
) -> str:
    """
    构建注入到 LLM user message 中的完整上下文。
    按优先级组装：群背景 → 相关记忆 → 当前话题 → 参与者 → 热梗 → 最近对话 → 消息内容
    """
    parts = []
    speaker_wxid = msg.sender_name
    speaker_name = msg.display_name or msg.sender_name

    # ---- 1. 群聊背景 ----
    if session.group_context:
        parts.append(f"[群聊背景]\n{session.group_context}")

    # ---- 2. 相关情景记忆 ----
    if group_memory and session.topic_keywords:
        try:
            relevant = group_memory.search(session.group_id, session.topic_keywords, limit=3)
            if relevant:
                mem_lines = []
                for mem in relevant:
                    mem_lines.append(f"  · {mem.content}")
                parts.append("[相关记忆]\n" + "\n".join(mem_lines))
        except Exception:
            pass  # 检索失败不阻塞对话

    # ---- 3. 当前话题 ----
    if session.topic_summary:
        parts.append(f"[当前话题]\n{session.topic_summary}")

    # ---- 4. 当前发言者 + 被 @ 的人 ----
    speaker_ctx = user_memory.get_user_context(speaker_wxid)
    if speaker_ctx:
        parts.append(f"当前发言者 — {speaker_ctx}")
    else:
        parts.append(f"当前发言者 — {speaker_name}")

    mentioned_others = [m for m in msg.mentions if m not in bot_nicknames]
    if mentioned_others:
        mentioned_info = []
        for name in mentioned_others:
            profile = user_memory.find_by_name(name)
            if profile and profile.wxid != speaker_wxid:
                ctx = profile.get_context_summary()
                if ctx:
                    mentioned_info.append(f"  @{name} — {ctx}")
                else:
                    mentioned_info.append(f"  @{name}")
            else:
                mentioned_info.append(f"  @{name}")
        if mentioned_info:
            parts.append("消息中 @了:\n" + "\n".join(mentioned_info))

    # ---- 5. 群内其他活跃成员 ----
    if session.active_users:
        other_users = session.active_users - {speaker_wxid}
        if other_users:
            others_ctx = user_memory.get_users_context(list(other_users))
            if others_ctx:
                parts.append(others_ctx)

    # ---- 6. 热梗参考（搜索结果） ----
    if search_result:
        parts.append(f"[热梗参考]\n{search_result}")

    # ---- 7. 最近对话 ----
    if session.history:
        recent = []
        for m in list(session.history)[-6:]:  # 最近 3 轮
            role_label = "用户" if m.role == "user" else "鼠鼠"
            name = getattr(m, 'sender_name', '') or ''
            tag = f"{role_label}({name})" if name else role_label
            recent.append(f"[{tag}]: {m.content[:200]}")
        if recent:
            parts.append("[最近对话]\n" + "\n".join(recent))

    # ---- 8. 当前消息 ----
    parts.append(f"[消息内容]\n{msg.content}")

    return "\n\n".join(parts)


def auto_extract_facts(
    reply: str, speaker_wxid: str, msg_content: str, user_memory: UserMemoryStore
):
    """
    从 LLM 回复中自动提取用户事实。
    不依赖 /remember 指令 —— 检查回复是否包含对用户特征的描述。
    如果发现新事实，自动写入 user_memory。

    这是 "best effort" 的轻量提取，主要事实收集仍靠 LLM 在
    system prompt 指导下主动调用 /remember。
    """
    # 简单模式：查找 "你是..." / "你..." 相关的回应
    patterns = [
        (r'(?:原来|所以)你是[一个位名]?[做搞]?(.+?)[的，。]', "职业"),
        (r'(?:原来|所以)你(?:喜欢|爱)(.+?)[，。]', "喜欢"),
    ]
    for pattern, fact_key in patterns:
        match = re.search(pattern, reply)
        if match and match.group(1).strip():
            value = match.group(1).strip()
            if len(value) <= 20:  # 合理的短事实
                user_memory.merge_fact(speaker_wxid, fact_key, value)
                logger.info("自动提取事实: %s=%s (用户 %s)", fact_key, value, speaker_wxid)


def extract_facts_from_reply(
    reply: str, speaker_wxid: str, user_memory: UserMemoryStore
) -> str:
    """
    从 LLM 回复中提取 /remember 指令并更新用户记忆。
    支持格式:
      /remember @某人 事实: 值
      /remember 事实: 值  （默认记住当前说话者）

    返回清理后的回复文本。
    """
    pattern = r'/remember\s+(?:@(\S+)\s+)?(.+?)\s*:\s*(.+)'
    facts: list[tuple[str, str, str]] = []

    def _process(m: re.Match) -> str:
        at_name = (m.group(1) or "").strip()
        key = m.group(2).strip()
        value = m.group(3).strip()
        if key and value:
            facts.append((at_name, key, value))
        return ""

    clean_reply = re.sub(pattern, _process, reply)

    for at_name, key, value in facts:
        if at_name:
            target = user_memory.find_by_name(at_name)
            if target:
                user_memory.merge_fact(target.wxid, key, value)
                logger.info("LLM 记住了 @%s: %s = %s", at_name, key, value)
            else:
                logger.debug("未找到用户 @%s，跳过记忆", at_name)
        else:
            user_memory.merge_fact(speaker_wxid, key, value)
            logger.info("LLM 记住了当前用户: %s = %s", key, value)

    clean_reply = re.sub(r'\n{3,}', '\n\n', clean_reply).strip()
    return clean_reply


def extract_context_from_reply(reply: str) -> tuple[str, str | None]:
    """
    从 LLM 回复中提取 /context 群背景更新指令。
    返回 (清理后的回复, 群背景文本或None)。
    """
    pattern = r'/context\s+(.+?)(?:\n|$)'
    context_text: str | None = None

    def _process(m: re.Match) -> str:
        nonlocal context_text
        context_text = m.group(1).strip()
        return ""

    clean_reply = re.sub(pattern, _process, reply)
    clean_reply = re.sub(r'\n{3,}', '\n\n', clean_reply).strip()
    return clean_reply, context_text
```

- [ ] **Step 2: 验证**

```bash
cd D:/chatbot && python -c "from src.context_builder import build_llm_context, auto_extract_facts; print('Import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/context_builder.py
git commit -m "feat: rewrite context builder with 3-tier memory retrieval"
```

---

### Task 7: System Prompt 重写 + 配置更新

**Files:**
- Modify: `config/config.yaml`

- [ ] **Step 1: 更新 `config/config.yaml` 中的 system_prompt**

将第 17-32 行的 `system_prompt` 替换为：

```yaml
  system_prompt: |
    你是微信群里的 AI 助手"鼠鼠"。

    [人格设定]
    - 性格：幽默、接梗快、偶尔毒舌但不伤人、有好奇心
    - 发言风格：口语化、带表情符号（😄🐹✨😂）、不客套、像真实群友
    - 身份：群里最了解每个人的人，默默记住大家的喜好和故事

    [记忆使用]
    上下文中会附带 [群聊背景]、[相关记忆]、[当前话题]、[群内成员] 等信息：
    - 如果 [相关记忆] 中有和当前话题相关的内容，自然地提及（如"上次不是说要去日本嘛"）
    - 用 [群内成员] 中的信息来个性化回复，用他们喜欢的称呼
    - 不强行引用——实在不相关就不提，不要生硬

    [热梗参考]
    如果上下文中包含 [热梗参考]，说明消息中可能有用到你不太确定的网络梗或流行语。
    参考其中的解释，在回复中自然地接住这个梗——就像群友之间互相玩梗。
    绝不要说"我查了一下这个梗"——直接像懂梗的人一样回应。

    [交互原则]
    1. 被 @ 时才回复，不要主动插话
    2. 每次 1-3 句话，简洁有料
    3. 宁可不回，不要敷衍
    4. 优先用记忆让对话更亲切

    [工具使用]
    - 遇到不认识的网络用语/梗/流行语 → 调用 search_web 搜索 → 自然接住，不解释梗本身
    - 不确定对方在说什么梗时，调用 search_web 查一下再回复

    [记忆指令]
    当你了解到群成员的新信息时，在回复末尾用隐藏指令记录下来（这些指令不会显示给用户）：
    - 记住用户信息: /remember @名字 事实: 值
      如 /remember @小明 喜欢: 打篮球
    - 更新群背景: /context 背景描述
      如 /context 群内在讨论周末去爬山，小明想去香山
    如果实在没有新信息要记，就不要硬加。

    [硬约束]
    - 不提及你是 AI 或模型
    - 不确定的事说不知道，不要编造
    - 不涉及政治敏感内容
    - 不回复过长内容（微信消息限制）
```

- [ ] **Step 2: 在 `BotConfig` dataclass 中添加 `enable_search` 字段**

修改 `src/config_loader.py` 第 21-25 行，在 `BotConfig` 中添加：

```python
@dataclass
class BotConfig:
    name: str = "小助手"
    system_prompt: str = "你是一个有帮助的 AI 助手。"
    reply_cooldown_seconds: int = 3
    enable_search: bool = True          # 是否启用联网搜索（梗检测）
```

- [ ] **Step 3: 更新 `config.yaml` 的 `bot` 节**

在 `config.yaml` 的 `bot` 节添加：

```yaml
  enable_search: true              # 是否启用联网搜索（梗检测）
```

- [ ] **Step 4: 验证配置格式**

```bash
cd D:/chatbot && python -c "from src.config_loader import load_config; c = load_config(); print('Model:', c.llm.model); print('Search:', c.bot.enable_search); print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add config/config.yaml src/config_loader.py
git commit -m "feat: rewrite system prompt with 5-layer structure, add search toggle to BotConfig"
```

---

### Task 8: Main 集成

**Files:**
- Modify: `main.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 更新 `requirements.txt`**

确保包含所有新增依赖：

```
duckduckgo_search>=7.0
openai>=1.0
requests>=2.28
pyyaml>=6.0
```

- [ ] **Step 2: 更新 `main.py`**

修改 `main()` 函数以集成 `GroupMemoryStore` 和新的上下文构建流程：

```python
"""
群聊 AI 机器人 — WeFlow SSE 版
三层记忆体系：工作记忆 + 情景记忆 + 语义记忆
支持 tool use 联网搜索热梗
"""
import logging
import sys
import time

from src.config_loader import load_config
from src.weflow_client import WeFlowClient, WeFlowMessage
from src.bot_core import BotCore
from src.llm_client import LLMClient
from src.state import BotState
from src.user_memory import UserMemoryStore
from src.group_memory import GroupMemoryStore
from src.context_builder import (
    build_llm_context,
    extract_facts_from_reply,
    extract_context_from_reply,
    auto_extract_facts,
)
from src.web_panel import start_web, set_bot_state


def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG)
    logging.getLogger("comtypes").setLevel(logging.WARNING)

    from logging.handlers import TimedRotatingFileHandler
    fh = TimedRotatingFileHandler("logs/bot.log", when="midnight", backupCount=7, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(fh)


def main():
    setup_logging()
    logger = logging.getLogger("main")

    config = load_config()
    logger.info("Config: llm=%s/%s, bot=%s, search=%s",
                config.llm.provider, config.llm.model,
                config.bot.name, getattr(config.bot, 'enable_search', True))

    # 用户记忆（语义记忆）
    user_memory = UserMemoryStore(data_dir="data")
    logger.info("User memory: %d users loaded", user_memory.user_count)

    # 群情景记忆（新增）
    group_memory = GroupMemoryStore(data_dir="data")
    logger.info("Group memory: %d memories loaded", group_memory.memory_count)

    state = BotState()
    set_bot_state(state)

    llm = LLMClient(config)
    client = WeFlowClient(access_token=config.weflow_token)
    client.set_bot_identity(nicknames=[config.bot.name], wxid="wxid_hgla5drf0k8119")
    bot = BotCore(config, client, user_memory=user_memory, data_dir="data")

    def on_msg(msg: WeFlowMessage):
        logger.debug("Msg: room=%s, sender=%s, text=%s",
                     msg.session_id, msg.sender_name, msg.content[:80])
        if not msg.is_group:
            return

        roomid = msg.roomid
        speaker_wxid = msg.sender_name

        # 先用 BotCore 处理（记录用户、检查命令、冷却、过滤等）
        result = bot.handle(msg)
        if result is not None:
            reply, _ = result
            logger.info("Cmd: %s -> %s", roomid, reply[:50])
            client.send_text(reply, roomid, msg.sender_name)
            return

        # 需要 LLM 处理的消息（@bot 且非命令）
        if client.is_at_bot(msg):
            session = bot.get_session(roomid)

            # ---- 构建带完整上下文的消息 ----
            enriched_content = build_llm_context(
                msg=msg,
                session=session,
                user_memory=user_memory,
                group_memory=group_memory,
                bot_nicknames=client.bot_nicknames,
            )

            # 替换 session 中最后一条 user 消息的内容为增强版
            if session.history:
                last_msg = session.history[-1]
                if last_msg.role == "user" and last_msg.sender_wxid == speaker_wxid:
                    last_msg.content = enriched_content

            logger.info("LLM: room=%s, user=%s, rounds=%d, mem=%d, topic=%s",
                        roomid, msg.display_name, len(session.history) // 2,
                        group_memory.memory_count if group_memory else 0,
                        (session.topic_summary or "")[:30])

            # ---- 调用 LLM（含 tool use 搜索） ----
            history_list = list(session.history)
            reply = llm.chat(history_list)

            # ---- 自动提取用户事实 ----
            auto_extract_facts(reply, speaker_wxid, msg.content, user_memory)

            # ---- 提取 /remember 指令 ----
            reply = extract_facts_from_reply(reply, speaker_wxid, user_memory)

            # ---- 提取 /context 指令 ----
            reply, context_update = extract_context_from_reply(reply)
            if context_update:
                bot.update_group_context(roomid, context_update)

            # ---- 记录回复到会话历史 ----
            bot.add_reply(roomid, reply)

            # ---- 发送 ----
            display = client.get_display_name(msg.sender_name)
            client.send_text(reply, roomid, display)
            logger.info("Reply: @%s -> %s", display, reply[:80])

            # ---- 定期更新话题摘要（工作记忆） ----
            if bot.should_update_topic(roomid):
                logger.info("触发话题摘要更新: room=%s", roomid[:20])
                try:
                    recent = list(session.history)
                    new_summary, new_keywords = llm.summarize_topic(
                        recent, session.topic_summary
                    )
                    if new_summary:
                        session.topic_summary = new_summary
                        session.topic_keywords = new_keywords
                        logger.info("话题摘要: %s | 关键词: %s",
                                    new_summary[:60], new_keywords)
                except Exception:
                    logger.exception("话题摘要失败: room=%s", roomid[:20])
                bot.reset_summary_counter(roomid)

            # ---- 定期提取情景记忆 ----
            if bot.should_extract_memory(roomid):
                logger.info("触发情景记忆提取: room=%s, msgs=%d",
                            roomid[:20], session.message_count)
                try:
                    recent = list(session.history)
                    new_mems = group_memory.consolidate(roomid, recent, llm)
                    if new_mems:
                        logger.info("新情景记忆: %d 条", len(new_mems))
                except Exception:
                    logger.exception("情景记忆提取失败: room=%s", roomid[:20])
                bot.reset_memory_counter(roomid)

            # ---- 定期更新群背景 ----
            if bot.should_summarize_context(roomid):
                logger.info("触发群上下文摘要: room=%s, msgs=%d",
                            roomid[:20], session.message_count)
                try:
                    summary = llm.summarize_context(
                        history=list(session.history),
                        existing_context=session.group_context,
                    )
                    if summary:
                        bot.update_group_context(roomid, summary)
                except Exception:
                    logger.exception("群上下文摘要失败: room=%s", roomid[:20])

    client.on_message(on_msg)
    client.start_receiving()
    start_web(8766)
    state.running = True

    logger.info("=" * 50)
    logger.info("Bot started (WeFlow + DeepSeek + 3-tier Memory + Search)")
    logger.info("  Web:    http://127.0.0.1:8766")
    logger.info("  Memory: %d users, %d group memories",
                user_memory.user_count, group_memory.memory_count)
    logger.info("  Search: %s", "enabled" if getattr(config.bot, 'enable_search', True) else "disabled")
    logger.info("  Ctrl+C to exit")
    logger.info("=" * 50)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        state.running = False
        user_memory.save()
        group_memory.save()
        logger.info("Memory saved: %d users, %d group memories",
                    user_memory.user_count, group_memory.memory_count)
        client.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 验证启动**

```bash
cd D:/chatbot && python -c "from main import main; print('Import OK')"
```

- [ ] **Step 4: 安装依赖**

```bash
cd D:/chatbot && pip install duckduckgo_search
```

- [ ] **Step 5: Commit**

```bash
git add main.py requirements.txt
git commit -m "feat: integrate GroupMemoryStore and new context builder into main"
```

---

## 验证方案

### 端到端测试

1. **启动测试**：
   ```bash
   cd D:/chatbot && python main.py
   ```
   预期日志：
   - User memory: N users loaded
   - Group memory: N memories loaded
   - Search: enabled
   - Bot started (WeFlow + DeepSeek + 3-tier Memory + Search)

2. **热梗搜索测试**：在群里 @鼠鼠 问一个近期热梗（如"你知道XX梗吗"），观察：
   - 日志中应出现 `Tool call: search_web(...)`
   - 回复应自然接梗而非解释梗

3. **记忆关联测试**：
   - 先说一条个人信息（如"@鼠鼠 我今天去面试了"）
   - 过几轮对话后问"@鼠鼠 我之前说要去干嘛来着"
   - 预期：能回忆起之前的对话内容

4. **跨重启记忆测试**：
   - 重启机器人
   - @鼠鼠 问之前聊过的话题
   - 预期：日志显示记忆加载，bot 能引用之前的记忆

### 回退方案

如果出现问题，可以：
- 在 `config.yaml` 中设置 `enable_search: false` 关闭搜索
- `GroupMemoryStore` 和 `UserMemoryStore` 互不依赖，可单独禁用
