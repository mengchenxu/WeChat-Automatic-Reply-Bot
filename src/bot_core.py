"""
Bot 核心 �?消息路由、命令系统、多群会话隔离�?"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from src.weflow_client import WeFlowMessage
from src.config_loader import AppConfig

logger = logging.getLogger(__name__)


# 单条对话记录
@dataclass
class ChatMessage:
    role: str          # "user" | "assistant"
    content: str


# 每个群的会话上下�?@dataclass
class GroupSession:
    group_id: str
    history: deque = field(default_factory=lambda: deque(maxlen=20))  # deque[ChatMessage]
    last_reply_at: float = 0.0  # 上次回复时间戳（用于冷却�?

class BotCore:
    """
    Bot 核心逻辑�?    1. 消息过滤 �?只处理群聊中 @bot 的消�?    2. 命令解析 �?/help /reset /status
    3. 多群会话隔离 �?每个群独立维护对话历�?    """

    def __init__(self, config: AppConfig, wechat_client):
        self.config = config
        self.client = wechat_client
        self.bot_name = config.bot.name
        self.cooldown = config.bot.reply_cooldown_seconds
        # 群会�? {group_id: GroupSession}
        self._sessions: Dict[str, GroupSession] = {}

    # ----------------------------------------------------------------
    # 入口：处理一条消息，返回要不要回�?+ 回复内容
    # ----------------------------------------------------------------
    def handle(self, msg: WeFlowMessage) -> Optional[Tuple[str, str]]:
        """
        处理消息。返�?(reply_text, roomid) �?None�?        None 表示无需回复�?        """

        # ---- 1. 仅群�?----
        if not msg.is_group:
            return None

        roomid = msg.roomid

        # ---- 2. 群过滤（白名�?黑名单） ----
        if not self._group_allowed(msg.roomid):
            return None

        # ---- 3. @bot 检�?----
        content = msg.content.strip()
        is_at_bot = self._is_at_bot(msg)

        # ---- 4. 命令优先 ----
        if is_at_bot and content.startswith("/"):
            return self._handle_command(content, roomid)

        # ---- 5. �?@ 不回�?----
        if not is_at_bot:
            return None

        # ---- 6. 冷却检�?----
        session = self._get_session(roomid)
        elapsed = time.time() - session.last_reply_at
        if elapsed < self.cooldown:
            logger.debug("�?%s 回复冷却�?(%.1fs < %ds)", roomid, elapsed, self.cooldown)
            return None

        # ---- 7. 添加用户消息到历史，返回�?LLM 处理 ----
        clean = self._clean_at_text(msg)
        session.history.append(ChatMessage(role="user", content=clean))
        session.last_reply_at = time.time()

        # 返回 None 表示需�?LLM 处理（调用方负责�?        # 把会话历史暴露给调用�?        return None

    def get_history(self, roomid: str) -> list:
        """获取某个群的对话历史，供 LLM 使用�?""
        session = self._get_session(roomid)
        return list(session.history)

    def add_reply(self, roomid: str, reply: str) -> None:
        """LLM 回复后，将回复加入该群的对话历史�?""
        session = self._get_session(roomid)
        session.history.append(ChatMessage(role="assistant", content=reply))

    # ----------------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------------
    def _is_at_bot(self, msg: WeFlowMessage) -> bool:
        """判断消息是否 @了机器人（基于内容检测）�?""
        if self.bot_name in msg.content:
            return True
        return False

    def _clean_at_text(self, msg: WeFlowMessage) -> str:
        """去掉 @bot 部分，返回干净的用户问题�?""
        text = msg.content.strip()
        import re
        text = re.sub(r"@[^\s]+\s*", "", text).strip()
        return text

    def _group_allowed(self, roomid: str) -> bool:
        """群白名单/黑名单过滤�?""
        wl = self.config.groups.whitelist
        bl = self.config.groups.blacklist
        # 白名单非空时，只响应白名单中的群
        if wl and roomid not in wl:
            return False
        # 黑名�?        if bl and roomid in bl:
            return False
        return True

    def _get_session(self, roomid: str) -> GroupSession:
        if roomid not in self._sessions:
            max_history = self.config.session.max_history_rounds * 2  # user+assistant 各一�?            self._sessions[roomid] = GroupSession(
                group_id=roomid,
                history=deque(maxlen=max_history),
            )
        return self._sessions[roomid]

    # ----------------------------------------------------------------
    # 命令处理
    # ----------------------------------------------------------------
    def _handle_command(self, content: str, roomid: str) -> Optional[Tuple[str, str]]:
        """处理 /xxx 命令。返�?(reply, roomid) �?None�?""
        cmd = content.split()[0].lower()

        if cmd == "/help":
            return (self._help_text(), roomid)

        if cmd == "/reset":
            self._sessions.pop(roomid, None)
            return ("�?对话已重置，我忘记了之前聊过什么�?, roomid)

        if cmd == "/status":
            session = self._get_session(roomid)
            rounds = len(session.history) // 2
            return (f"📊 当前会话: {rounds} 轮对话，冷却时间 {self.cooldown}s", roomid)

        return (f"未知命令: {cmd}，发�?/help 查看可用命令", roomid)

    def _help_text(self) -> str:
        return (
            f"🤖 {self.bot_name} 使用说明:\n"
            f"  @�?+ 任意问题 �?和我聊天\n"
            f"  /help  �?显示此帮助\n"
            f"  /reset �?重置对话记忆\n"
            f"  /status �?查看当前状态\n"
        )
