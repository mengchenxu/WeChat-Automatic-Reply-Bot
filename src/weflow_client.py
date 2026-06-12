"""
WeFlow 接入层 — REST API 轮询版。
"""
import json, logging, threading, time, requests
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class WeFlowMessage:
    def __init__(self, data: dict, session_type: str = "", session_name: str = ""):
        import re
        self.content = data.get("content", "") or ""
        self.sender_name = data.get("senderUsername", "") or ""
        self.rawid = str(data.get("localId", "") or data.get("createTime", time.time()))
        self.session_id = data.get("talker", "") or ""  # will be set by caller
        self.session_type = session_type
        self.group_name = session_name
        self.raw = data
        # 提取所有 @mention（用于 LLM 上下文）
        self.mentions = re.findall(r'@(\S+)', self.content or "")
        # 从 rawContent 提取显示名
        raw_content = data.get("rawContent", "") or ""
        if ":" in raw_content and "\n" in raw_content:
            self.display_name = raw_content.split(":\n")[0]
        else:
            self.display_name = self.sender_name

    @property
    def is_group(self) -> bool:
        return bool(self.session_type == "group" or "@chatroom" in self.session_id)

    @property
    def roomid(self) -> str:
        return self.session_id


class WeFlowClient:
    def __init__(self, base_url: str = "http://127.0.0.1:5031", access_token: str = "", poll_interval: float = 2.0):
        self.base_url = base_url
        self.access_token = access_token
        self.poll_interval = poll_interval
        self._running = False
        self._callback: Optional[Callable[[WeFlowMessage], None]] = None
        self._seen_ids: set = set()
        self.bot_nicknames: list = []
        self.bot_wxid: str = ""
        self._sender = None
        self._start_ts = int(time.time())  # 启动时间，过滤旧消息

    def set_bot_identity(self, nicknames: list, wxid: str = ""):
        self.bot_nicknames = nicknames
        self.bot_wxid = wxid
        self._build_name_cache()

    def _build_name_cache(self):
        """从联系人 API 构建 wxid → 显示名 映射。"""
        self._name_cache = {}
        try:
            resp = self._api_get("/api/v1/contacts")
            if resp and "contacts" in resp:
                for c in resp["contacts"]:
                    wxid = c.get("username", "")
                    # 优先用备注名，其次 displayName，最后 nickname
                    name = c.get("remark") or c.get("displayName") or c.get("nickname") or wxid
                    if wxid:
                        self._name_cache[wxid] = name
            logger.info("Name cache: %d entries", len(self._name_cache))
        except Exception:
            pass

    def get_display_name(self, wxid: str) -> str:
        return self._name_cache.get(wxid, wxid)

    def is_at_bot(self, msg: WeFlowMessage) -> bool:
        # 只匹配 @鼠鼠 格式，避免普通对话中的"鼠鼠"触发
        for nick in self.bot_nicknames:
            if nick and f"@{nick}" in msg.content:
                return True
        return False

    def start_receiving(self):
        self._running = True
        threading.Thread(target=self._poll_loop, daemon=True, name="weflow-poll").start()
        logger.info("WeFlow REST polling started")

    def on_message(self, callback):
        self._callback = callback

    def _poll_loop(self):
        while self._running:
            try:
                self._poll()
            except Exception:
                logger.exception("Poll error")
            time.sleep(self.poll_interval)

    def _poll(self):
        sessions = self._api_get("/api/v1/sessions")
        if not sessions:
            return

        for sess in sessions.get("sessions", []):
            talker = sess.get("username", "")
            stype = sess.get("sessionType", "")
            sname = sess.get("displayName", "")
            unread = sess.get("unreadCount", 0)

            if not talker:
                continue
            if stype != "group" and "@chatroom" not in talker:
                continue

            resp = self._api_get(f"/api/v1/messages?talker={talker}&media=false")
            if not resp:
                continue

            for mdata in resp.get("messages", []):
                msg = WeFlowMessage(mdata, session_type=stype, session_name=sname)
                msg.session_id = talker

                if not msg.content.strip():
                    continue

                # 忽略启动前的旧消息（createTime 是 Unix 秒级时间戳）
                create_time = mdata.get("createTime", 0)
                if create_time and create_time < self._start_ts - 10:
                    continue

                key = msg.rawid
                if key in self._seen_ids:
                    continue
                self._seen_ids.add(key)
                if len(self._seen_ids) > 5000:
                    self._seen_ids = set(list(self._seen_ids)[-2500:])

                # 自回过滤：按 wxid 或昵称
                if msg.sender_name == self.bot_wxid or msg.sender_name in self.bot_nicknames:
                    continue

                # 只响应 @鼠鼠 的消息
                if not self.is_at_bot(msg):
                    continue

                logger.info("Msg: room=%s, sender=%s, text=%s", talker, msg.sender_name, msg.content[:80])
                if self._callback:
                    self._callback(msg)

    def _api_get(self, path: str):
        try:
            sep = "&" if "?" in path else "?"
            url = f"{self.base_url}{path}{sep}access_token={self.access_token}"
            r = requests.get(url, timeout=10)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def send_text(self, text: str, receiver: str, at_sender: str = "") -> bool:
        if self._sender is None:
            try:
                from src.uia_sender import UiaSender
                self._sender = UiaSender()
                logger.info("UIA sender ready")
            except Exception:
                return False
        try:
            return self._sender.send_text(receiver, text, at_sender)
        except Exception:
            return False

    def stop(self):
        self._running = False
