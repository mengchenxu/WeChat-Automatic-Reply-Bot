"""
统一客户端 — UIA 轮询接收 + UIA 自动化发送。
纯 UIA 方案，不依赖 WeFlow、不依赖 DLL 注入，适配微信 4.x。
"""
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class WeChatMessage:
    """统一消息对象。"""
    content: str
    session_name: str       # 群名/联系人名
    session_type: str       # "group" / "private"
    sender_name: str        # 群内发送者名
    rawid: str = ""         # 去重 ID

    @property
    def is_group(self) -> bool:
        return self.session_type == "group"

    @property
    def roomid(self) -> str:
        return self.session_name


class WeChatClient:
    """
    统一微信客户端：
    - 收：UIA 轮询聊天窗口新消息
    - 发：UIA 自动化发送（Ctrl+F 搜索 + ValuePattern/剪贴板输入 + Enter）
    """

    def __init__(self, poll_interval: float = 1.0):
        self.poll_interval = poll_interval
        self.bot_nicknames: list = []
        self.bot_wxid: str = ""

        self._running = False
        self._callback: Optional[Callable[[WeChatMessage], None]] = None
        self._receiver = None
        self._sender = None
        self._seen: set = set()

    def set_bot_identity(self, nicknames: list, wxid: str = ""):
        self.bot_nicknames = nicknames
        self.bot_wxid = wxid

    def is_at_bot(self, msg: WeChatMessage) -> bool:
        for nick in self.bot_nicknames:
            if nick and nick in msg.content:
                return True
        return False

    # ----------------------------------------------------------------
    # 接收
    # ----------------------------------------------------------------
    def start_receiving(self) -> None:
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True, name="recv").start()
        logger.info("UIA 接收线程已启动，轮询间隔 %.1fs", self.poll_interval)

    def _recv_loop(self) -> None:
        """轮询微信窗口获取新消息。"""
        from src.uia_receiver import UiaReceiver
        self._receiver = UiaReceiver(poll_interval=self.poll_interval)
        self._receiver.on_message(self._on_uia_message)
        self._receiver.start()

        while self._running:
            import time
            time.sleep(1)

    def _on_uia_message(self, msg) -> None:
        """将 UiaReceiver 消息转为本层 WeChatMessage。"""
        wx_msg = WeChatMessage(
            content=msg.content,
            session_name=msg.session_name,
            session_type=msg.session_type,
            sender_name=msg.sender_name,
            rawid=f"{msg.session_name}|{msg.content[:80]}",
        )

        # 去重
        if wx_msg.rawid in self._seen:
            return
        self._seen.add(wx_msg.rawid)
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2500:])

        # 自回过滤
        for nick in self.bot_nicknames:
            if nick and nick in msg.sender_name:
                return

        if self._callback:
            self._callback(wx_msg)

    def on_message(self, callback: Callable[[WeChatMessage], None]) -> None:
        self._callback = callback

    # ----------------------------------------------------------------
    # 发送
    # ----------------------------------------------------------------
    def send_text(self, text: str, receiver: str, at_sender: str = "") -> bool:
        if self._sender is None:
            try:
                from src.uia_sender import UiaSender
                self._sender = UiaSender()
                logger.info("UIA 发送器初始化成功")
            except Exception:
                logger.exception("UIA 发送器初始化失败")
                return False

        try:
            return self._sender.send_text(receiver, text)
        except Exception:
            logger.exception("UIA 发送失败")
            return False

    # ----------------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------------
    def stop(self) -> None:
        self._running = False
        if self._receiver:
            self._receiver.stop()
        logger.info("客户端已停止")
