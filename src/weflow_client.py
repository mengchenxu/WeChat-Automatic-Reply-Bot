"""
WeFlow 接入层 — REST API 轮询版。
1. 轮询 /api/v1/sessions 检测未读消息
2. 调用 /api/v1/messages?talker= 获取消息
3. UIA 自动发送回复
"""
import json
import logging
import threading
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)


class WeFlowMessage:
    def __init__(self, data: dict):
        self.content = data.get("content", "") or ""
        self.session_id = data.get("talker", "") or ""
        self.session_type = data.get("sessionType", "") or ""
        self.group_name = data.get("displayName", "") or ""
        self.sender_name = data.get("senderName", "") or data.get("talkerName", "") or ""
        self.sender_id = data.get("senderId", "") or ""
        self.rawid = data.get("id", "") or str(data.get("timestamp", time.time()))
        self.timestamp = data.get("timestamp", 0) or 0
        self.raw = data

    @property
    def is_group(self) -> bool:
        return bool(self.session_type == "group" or "@chatroom" in self.session_id)

    @property
    def roomid(self) -> str:
        return self.session_id


class WeFlowClient:
    def __init__(self, base_url: str = "http://127.0.0.1:5031", access_token: str = "", poll_interval: float = 1.0):
        self.base_url = base_url
        self.access_token = access_token
        self.poll_interval = poll_interval
        self._running = False
        self._callback: Optional[Callable[[WeFlowMessage], None]] = None
        self._seen_ids: set = set()
        self.bot_nicknames: list = []
        self._sender = None

    def set_bot_identity(self, nicknames: list, wxid: str = ""):
        self.bot_nicknames = nicknames

    def is_at_bot(self, msg: WeFlowMessage) -> bool:
        for nick in self.bot_nicknames:
            if nick and nick in msg.content:
                return True
        return False

    # ----------------------------------------------------------------
    # 接收 — REST 轮询
    # ----------------------------------------------------------------
    def start_receiving(self):
        self._running = True
        threading.Thread(target=self._poll_loop, daemon=True, name="weflow-poll").start()
        logger.info("WeFlow REST 轮询已启动")

    def on_message(self, callback):
        self._callback = callback

    def _poll_loop(self):
        while self._running:
            try:
                self._poll()
            except Exception:
                logger.exception("轮询异常")
            time.sleep(self.poll_interval)

    def _poll(self):
        # 1. 获取会话列表
        sessions = self._get_sessions()
        if not sessions:
            return

        # 2. 遍历有消息的群聊
        for sess in sessions:
            talker = sess.get("username", "")
            stype = sess.get("sessionType", "")

            if not talker:
                continue
            if stype != "group" and "@chatroom" not in talker:
                continue

            # 3. 获取消息
            msgs = self._get_messages(talker)
            for m in msgs:
                msg = WeFlowMessage(m)
                if not msg.content.strip():
                    continue
                key = msg.rawid or f"{talker}|{msg.content[:80]}"
                if key in self._seen_ids:
                    continue
                self._seen_ids.add(key)
                if len(self._seen_ids) > 10000:
                    self._seen_ids = set(list(self._seen_ids)[-5000:])

                # 自回过滤
                skip = False
                for nick in self.bot_nicknames:
                    if nick and nick in msg.sender_name:
                        skip = True
                        break
                if skip:
                    continue

                logger.info("新消息: room=%s, sender=%s, text=%s", talker, msg.sender_name, msg.content[:80])
                if self._callback:
                    self._callback(msg)

    def _get_sessions(self):
        try:
            url = f"{self.base_url}/api/v1/sessions?access_token={self.access_token}"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                return data.get("sessions", [])
        except Exception:
            pass
        return []

    def _get_messages(self, talker: str):
        try:
            url = f"{self.base_url}/api/v1/messages?access_token={self.access_token}&talker={talker}&media=false"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                msgs = data.get("messages", [])
                return sorted(msgs, key=lambda x: x.get("timestamp", 0))[-20:]
        except Exception:
            pass
        return []

    # ----------------------------------------------------------------
    # 发送
    # ----------------------------------------------------------------
    def send_text(self, text: str, receiver: str, at_sender: str = "") -> bool:
        if self._sender is None:
            try:
                from src.uia_sender import UiaSender
                self._sender = UiaSender()
                logger.info("UIA sender initialized")
            except Exception:
                logger.exception("UIA sender init failed")
                return False
        try:
            return self._sender.send_text(receiver, text)
        except Exception:
            logger.exception("UIA send failed")
            return False

    def stop(self):
        self._running = False
        logger.info("WeFlow client stopped")
