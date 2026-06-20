"""
Bot 核心 — 消息路由、命令系统、多群会话隔离、用户记忆集成。
"""
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Set

from src.weflow_client import WeFlowMessage
from src.config_loader import AppConfig

logger = logging.getLogger(__name__)


# 单条对话记录（增强版 — 带发送者信息）
@dataclass
class ChatMessage:
    role: str              # "user" | "assistant" | "system"
    content: str
    sender_name: str = ""  # 谁说的（user 消息时有值，显示名）
    sender_wxid: str = ""  # 发送者 wxid
    timestamp: float = 0.0 # 消息时间


# 每个群的会话上下文
@dataclass
class GroupSession:
    group_id: str
    history: deque = field(default_factory=lambda: deque(maxlen=20))  # deque[ChatMessage]
    last_reply_at: float = 0.0          # 上次回复时间戳（用于冷却）
    active_users: Set[str] = field(default_factory=set)  # 本群活跃用户 wxid 集合
    group_context: str = ""             # 群级别上下文摘要
    message_count: int = 0              # 本群已处理消息数（用于触发摘要）
    # 工作记忆字段
    topic_summary: str = ""               # 当前话题摘要
    topic_keywords: list = field(default_factory=list)  # 话题关键词
    message_since_summary: int = 0        # 距上次话题摘要的消息数
    message_since_memory: int = 0         # 距上次记忆提取的消息数


class BotCore:
    """
    Bot 核心逻辑：
    1. 消息过滤 — 只处理群聊中 @bot 的消息
    2. 命令解析 — /help /reset /status /whois /memory
    3. 多群会话隔离 — 每个群独立维护对话历史
    4. 用户追踪 — 记录群内活跃用户
    """

    def __init__(self, config: AppConfig, weflow_client, user_memory=None, data_dir: str = "data"):
        self.config = config
        self.client = weflow_client
        self.bot_name = config.bot.name
        self.cooldown = config.bot.reply_cooldown_seconds
        self.user_memory = user_memory  # UserMemoryStore，由 main.py 注入
        self.context_summary_interval = config.session.context_summary_interval
        self.data_dir = data_dir
        # 群会话: {group_id: GroupSession}
        self._sessions: Dict[str, GroupSession] = {}
        # 加载持久化的群上下文
        self._load_group_contexts()

    # ----------------------------------------------------------------
    # 入口：处理一条消息
    # ----------------------------------------------------------------
    def handle(self, msg: WeFlowMessage) -> Optional[Tuple[str, str]]:
        """
        处理消息。返回 (reply_text, roomid) 或 None。
        None 表示需要 LLM 处理（调用方负责）。
        """

        # ---- 1. 仅群聊 ----
        if not msg.is_group:
            return None

        roomid = msg.roomid

        # ---- 2. 群过滤（白名单/黑名单） ----
        if not self._group_allowed(roomid):
            return None

        # ---- 3. @bot 检测 ----
        content = msg.content.strip()
        is_at_bot = self._is_at_bot(msg)

        # ---- 4. 记录用户活动 ----
        speaker_wxid = msg.sender_name  # WeFlow 中 sender_name 即 wxid
        speaker_display = msg.display_name

        # 记录到用户记忆
        if self.user_memory:
            self.user_memory.record_message(speaker_wxid, speaker_display)

        # 记录到群活跃用户
        session = self._get_session(roomid)
        session.active_users.add(speaker_wxid)
        session.message_count += 1
        session.message_since_summary += 1
        session.message_since_memory += 1

        # ---- 5. 命令优先 ----
        if is_at_bot and content.startswith("/"):
            return self._handle_command(content, roomid, msg)

        # ---- 6. 非 @ 不回复 ----
        if not is_at_bot:
            return None

        # ---- 7. 冷却检查 ----
        elapsed = time.time() - session.last_reply_at
        if elapsed < self.cooldown:
            logger.debug("群 %s 回复冷却中 (%.1fs < %ds)", roomid, elapsed, self.cooldown)
            return None

        # ---- 8. 添加用户消息到历史（带发送者信息） ----
        clean = self._clean_at_text(msg)
        chat_msg = ChatMessage(
            role="user",
            content=clean,
            sender_name=speaker_display,
            sender_wxid=speaker_wxid,
            timestamp=time.time(),
        )
        session.history.append(chat_msg)
        session.last_reply_at = time.time()

        # 返回 None 表示需要 LLM 处理（调用方负责）
        return None

    def get_history(self, roomid: str) -> list:
        """获取某个群的对话历史，供 LLM 使用。"""
        session = self._get_session(roomid)
        return list(session.history)

    def add_reply(self, roomid: str, reply: str) -> None:
        """LLM 回复后，将回复加入该群的对话历史。"""
        session = self._get_session(roomid)
        session.history.append(ChatMessage(
            role="assistant",
            content=reply,
            timestamp=time.time(),
        ))

    def get_session(self, roomid: str) -> GroupSession:
        """获取群会话（供外部使用）。"""
        return self._get_session(roomid)

    # ----------------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------------
    def _is_at_bot(self, msg: WeFlowMessage) -> bool:
        """判断消息是否 @了机器人（精确匹配 @昵称）。"""
        if f"@{self.bot_name}" in msg.content:
            return True
        return False

    def _clean_at_text(self, msg: WeFlowMessage) -> str:
        """去掉 @bot 部分，返回干净的用户问题。"""
        text = msg.content.strip()
        import re
        text = re.sub(r"@[^\s]+\s*", "", text).strip()
        return text

    def _group_allowed(self, roomid: str) -> bool:
        """群白名单/黑名单过滤。"""
        wl = self.config.groups.whitelist
        bl = self.config.groups.blacklist
        if wl and roomid not in wl:
            return False
        if bl and roomid in bl:
            return False
        return True

    def _get_session(self, roomid: str) -> GroupSession:
        if roomid not in self._sessions:
            self._sessions[roomid] = GroupSession(
                group_id=roomid,
                history=deque(maxlen=20),
            )
        return self._sessions[roomid]

    # ----------------------------------------------------------------
    # 群上下文记忆
    # ----------------------------------------------------------------
    def should_summarize_context(self, roomid: str) -> bool:
        """检查是否应该触发群上下文摘要更新。"""
        session = self._get_session(roomid)
        # 每 N 条消息触发一次，且至少有 3 轮对话
        if session.message_count > 0 and session.message_count % self.context_summary_interval == 0:
            if len(session.history) >= 6:  # 至少 3 轮对话
                return True
        return False

    def update_group_context(self, roomid: str, context: str):
        """更新群级别上下文摘要。"""
        session = self._get_session(roomid)
        if context.strip():
            session.group_context = context.strip()
            logger.info("群 %s 上下文已更新 (%d 字): %s",
                        roomid[:20], len(context), context[:80])
            self._save_group_contexts()

    def get_group_context(self, roomid: str) -> str:
        """获取群上下文摘要。"""
        session = self._get_session(roomid)
        return session.group_context

    # ----------------------------------------------------------------
    # 话题追踪与记忆提取触发
    # ----------------------------------------------------------------
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

    def extract_context_from_reply(self, roomid: str, reply: str) -> Optional[str]:
        """
        从 LLM 回复中提取群上下文更新指令。
        支持格式: /context 群聊背景描述
        返回提取的上下文文本，或 None。
        """
        import re
        match = re.search(r'/context\s+(.+?)(?:\n|$)', reply)
        if match:
            return match.group(1).strip()
        return None

    # ----------------------------------------------------------------
    # 群上下文持久化
    # ----------------------------------------------------------------
    def _group_contexts_path(self) -> str:
        return os.path.join(self.data_dir, "group_contexts.json")

    def _save_group_contexts(self):
        """保存所有群上下文到磁盘。"""
        try:
            data = {}
            for roomid, session in self._sessions.items():
                if session.group_context:
                    data[roomid] = {
                        "context": session.group_context,
                        "message_count": session.message_count,
                    }
            if data:
                with open(self._group_contexts_path(), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.debug("已保存 %d 个群上下文", len(data))
        except Exception:
            logger.exception("保存群上下文失败")

    def _load_group_contexts(self):
        """从磁盘加载群上下文。"""
        path = self._group_contexts_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for roomid, d in data.items():
                session = self._get_session(roomid)
                session.group_context = d.get("context", "")
                session.message_count = d.get("message_count", 0)
            logger.info("已加载 %d 个群上下文", len(data))
        except Exception:
            logger.exception("加载群上下文失败")

    # ----------------------------------------------------------------
    # 命令处理
    # ----------------------------------------------------------------
    def _handle_command(self, content: str, roomid: str, msg: WeFlowMessage = None) -> Optional[Tuple[str, str]]:
        """处理 /xxx 命令。返回 (reply, roomid) 或 None。"""
        parts = content.split()
        cmd = parts[0].lower()

        if cmd == "/help":
            return (self._help_text(), roomid)

        if cmd == "/reset":
            self._sessions.pop(roomid, None)
            return ("✅ 对话已重置，我忘记了之前聊过什么。", roomid)

        if cmd == "/status":
            session = self._get_session(roomid)
            rounds = len(session.history) // 2
            users = len(session.active_users)
            mem_info = ""
            if self.user_memory:
                mem_info = f"，记忆了 {self.user_memory.user_count} 个用户"
            return (f"📊 当前会话: {rounds} 轮对话，{users} 个活跃成员{mem_info}，冷却 {self.cooldown}s", roomid)

        if cmd == "/whois":
            # /whois @某人 — 查看某用户的信息
            if msg and len(parts) > 1:
                target_name = parts[1].lstrip("@")
                if self.user_memory:
                    profile = self.user_memory.find_by_name(target_name)
                    if profile:
                        summary = profile.get_context_summary()
                        if summary:
                            return (f"📋 @{target_name}: {summary}", roomid)
                        else:
                            return (f"📋 @{target_name}: 暂无已知信息", roomid)
                    else:
                        return (f"❓ 没找到 @{target_name} 的信息", roomid)
                return ("⚠ 用户记忆功能未启用", roomid)
            return ("用法: /whois @某人", roomid)

        if cmd == "/memory":
            # 显示群上下文摘要
            session = self._get_session(roomid)
            ctx = session.group_context
            if ctx:
                return (f"🧠 群聊记忆:\n{ctx}", roomid)
            return ("🧠 暂无群聊记忆，多聊聊我就会慢慢了解你们～", roomid)

        if cmd == "/remember":
            # /remember @某人 事实: 值 — 手动教 bot 记住
            if self.user_memory and msg and len(parts) >= 3:
                # 格式: /remember @name key: value
                rest = " ".join(parts[1:])
                if rest.startswith("@"):
                    name_end = rest.index(" ")
                    target_name = rest[1:name_end]
                    fact_part = rest[name_end+1:].strip()
                    if ":" in fact_part:
                        key, value = fact_part.split(":", 1)
                        profile = self.user_memory.find_by_name(target_name)
                        if profile:
                            self.user_memory.update_fact(profile.wxid, key.strip(), value.strip())
                            return (f"✅ 记住了 @{target_name}: {key.strip()} = {value.strip()}", roomid)
                        return (f"❓ 没找到 @{target_name}", roomid)
            return ("用法: /remember @某人 事实: 值", roomid)

        return (f"未知命令: {cmd}，发送 /help 查看可用命令", roomid)

    def _help_text(self) -> str:
        return (
            f"🤖 {self.bot_name} 使用说明:\n"
            f"  @我 + 任意问题 — 和我聊天（我会记住你们）\n"
            f"  /help     — 显示此帮助\n"
            f"  /reset    — 重置对话记忆\n"
            f"  /status   — 查看当前状态\n"
            f"  /whois @某人 — 查看某用户的信息\n"
            f"  /memory   — 查看群聊记忆\n"
            f"  /remember @某人 事实:值 — 教我记得某事\n"
        )
