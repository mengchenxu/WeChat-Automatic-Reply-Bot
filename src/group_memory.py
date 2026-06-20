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
