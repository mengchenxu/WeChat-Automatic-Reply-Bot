"""
UIA 消息接收器 — 通过轮询微信 4.x 窗口的聊天列表，检测并读取新消息。
不依赖 WeFlow、不依赖 DLL 注入，纯 UIA 实现。
"""
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class UiaMessage:
    """UIA 接收到的一条消息。"""
    content: str
    session_name: str       # 群名或联系人名
    session_type: str       # "group" 或 "private"
    sender_name: str        # 群内发送者名（群聊时有值）
    timestamp: int = 0

    @property
    def is_group(self) -> bool:
        return self.session_type == "group"

    @property
    def roomid(self) -> str:
        return self.session_name


class UiaReceiver:
    """
    微信 4.x 消息接收器。

    原理：
    1. 定位微信主窗口
    2. 双击「聊天」图标回到聊天列表
    3. 遍历聊天列表，检测红点/数字标记判断是否有新消息
    4. 点击有消息的会话 → 读取消息列表 → 提取最新消息
    5. 解析消息内容（支持群聊 @ 和普通文本）
    """

    # 微信窗口的候选 ClassName（Qt 版 4.x 和 Electron 版 4.x）
    WINDOW_CLASSES = ["Qt51514QWindowIcon", "CefTopWindow", "Chrome_WidgetWin_0"]

    def __init__(self, poll_interval: float = 1.0):
        self.poll_interval = poll_interval
        self._auto = None
        self._window = None
        self._running = False
        self._callback: Optional[Callable[[UiaMessage], None]] = None
        self._seen: set = set()  # 已处理的消息去重

    # ----------------------------------------------------------------
    # 初始化
    # ----------------------------------------------------------------
    def _init_uia(self) -> bool:
        """初始化 UIA 并定位微信窗口。"""
        try:
            import uiautomation as auto
            self._auto = auto
        except ImportError:
            logger.error("请安装 uiautomation: pip install uiautomation")
            return False

        root = auto.GetRootControl()
        for w in root.GetChildren():
            name = w.Name or ""
            if "微信" in name or "WeChat" in name:
                self._window = w
                logger.info("找到微信窗口: '%s' (ClassName=%s)", name, w.ClassName)
                return True

        # 尝试按 ClassName 找
        for cls in self.WINDOW_CLASSES:
            try:
                w = auto.WindowControl(ClassName=cls, searchDepth=1)
                if w.Exists(1):
                    self._window = w
                    logger.info("找到微信窗口: ClassName=%s", cls)
                    return True
            except Exception:
                pass

        logger.error("未找到微信窗口，请确认微信已登录且窗口可见")
        return False

    # ----------------------------------------------------------------
    # 启动轮询
    # ----------------------------------------------------------------
    def start(self) -> None:
        if not self._init_uia():
            return
        self._running = True
        threading.Thread(target=self._poll_loop, daemon=True, name="uia-recv").start()
        logger.info("UIA 接收器已启动，轮询间隔 %.1fs", self.poll_interval)

    def on_message(self, callback: Callable[[UiaMessage], None]) -> None:
        self._callback = callback

    def stop(self) -> None:
        self._running = False

    # ----------------------------------------------------------------
    # 轮询主循环
    # ----------------------------------------------------------------
    def _poll_loop(self) -> None:
        while self._running:
            try:
                self._check_for_new_messages()
            except Exception:
                logger.exception("轮询异常")
            time.sleep(self.poll_interval)

    def _check_for_new_messages(self) -> None:
        """检查是否有新消息（简化版：只看当前聊天窗口）。"""
        if not self._window or not self._window.Exists(0.5):
            self._init_uia()
            return

        try:
            # 获取当前聊天窗口的消息列表
            chat_name = self._get_current_chat_name()
            if not chat_name:
                return

            messages = self._read_chat_messages()
            if not messages:
                return

            # 处理新消息
            for msg_text in messages:
                msg_key = f"{chat_name}|{msg_text[:100]}"
                if msg_key in self._seen:
                    continue
                self._seen.add(msg_key)
                if len(self._seen) > 1000:
                    self._seen = set(list(self._seen)[-500:])

                # 判断是群聊还是私聊
                is_group = self._is_group_chat()

                # 群聊解析发送者
                sender = ""
                if is_group:
                    sender = self._parse_group_sender(msg_text)
                    if sender:
                        content = msg_text[len(sender):].lstrip(":").lstrip("：").strip()
                    else:
                        content = msg_text
                else:
                    content = msg_text

                if self._callback:
                    msg = UiaMessage(
                        content=content,
                        session_name=chat_name,
                        session_type="group" if is_group else "private",
                        sender_name=sender,
                    )
                    self._callback(msg)

        except Exception:
            pass

    # ----------------------------------------------------------------
    # UIA 窗口操作
    # ----------------------------------------------------------------
    def _get_current_chat_name(self) -> Optional[str]:
        """获取当前聊天窗口的会话名称。"""
        try:
            # 微信 4.x 聊天标题在窗口顶部某个 TextControl 中
            for ctrl, _ in self._auto.WalkTree(self._window, lambda c: True, maxDepth=8):
                if ctrl.ControlTypeName == "TextControl":
                    name = ctrl.Name or ""
                    # 聊天标题通常较短，不会是长文本
                    if 2 <= len(name) <= 40 and "微信" not in name:
                        return name
        except Exception:
            pass
        return None

    def _is_group_chat(self) -> bool:
        """判断当前聊天是否为群聊。"""
        try:
            # 群聊窗口中通常有「群成员」相关的控件
            for ctrl, _ in self._auto.WalkTree(self._window, lambda c: True, maxDepth=10):
                name = ctrl.Name or ""
                if "群成员" in name or "成员" in name or "群聊" in name:
                    return True
        except Exception:
            pass
        return False

    def _read_chat_messages(self) -> list:
        """读取当前聊天窗口的最新消息文本列表。"""
        messages = []
        try:
            for ctrl, _ in self._auto.WalkTree(self._window, lambda c: True, maxDepth=12):
                if ctrl.ControlTypeName == "ListItemControl":
                    name = ctrl.Name or ""
                    if name and len(name) > 1:
                        # 过滤掉纯数字时间戳、系统提示等
                        if not re.match(r'^\d{1,2}:\d{2}$', name):
                            messages.append(name)
        except Exception:
            pass

        # 取最后几条（最新的消息在列表底部）
        return messages[-20:] if len(messages) > 20 else messages

    def _parse_group_sender(self, text: str) -> str:
        """尝试从群聊消息文本中解析发送者名称。
        微信 4.x 群聊消息格式通常是 "发送者名：消息内容" 或 "发送者名: 消息内容"。
        """
        match = re.match(r'^([^：:]{1,20})[：:]', text)
        if match:
            return match.group(1)
        return ""

    # ----------------------------------------------------------------
    # 发送辅助（直接在这里暴露，方便外部调用）
    # ----------------------------------------------------------------
    def switch_to_chat(self, name: str) -> bool:
        """切换到指定聊天会话。使用 Ctrl+F 搜索。"""
        try:
            import ctypes
            hwnd = None
            for cls in self.WINDOW_CLASSES:
                hwnd = ctypes.windll.user32.FindWindowW(cls, None)
                if hwnd:
                    break
            if not hwnd:
                return False

            # Ctrl+F
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x46, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x46, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.3)

            # 粘贴名称
            import pyperclip
            pyperclip.copy(name)
            time.sleep(0.1)
            ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
            ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.3)

            # Enter
            ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
            time.sleep(0.5)

            return True
        except Exception:
            return False
