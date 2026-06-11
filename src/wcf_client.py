"""
WCF 接入层 — 封装 wcferry，负责微信登录、消息收发。
"""
import queue
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from wcferry import Wcf, WxMsg

logger = logging.getLogger(__name__)


@dataclass
class WcfClient:
    """
    封装 wcferry.Wcf，提供：
    - 初始化 & 登录
    - 消息回调注册
    - 发送群聊文本（支持 @）
    """

    wcf: Wcf = field(default_factory=Wcf)
    _msg_queue: queue.Queue = field(default_factory=queue.Queue)
    _running: bool = False
    _callback: Optional[Callable[[WxMsg], None]] = None

    # ------------------------------------------------------------
    # 初始化 & 登录
    # ------------------------------------------------------------
    def login(self) -> bool:
        """初始化 WCF 并等待扫码登录。返回是否登录成功。"""
        logger.info("正在初始化 WCF...")

        # 1. 获取登录二维码（阻塞，等待扫码）
        qr_code = self.wcf.get_qrcode()
        if qr_code:
            logger.info("已获取登录二维码，请用微信扫码登录")
        else:
            logger.warning("获取二维码失败，可能已处于登录状态")

        # 2. 轮询等待登录完成
        logger.info("等待登录...")
        while True:
            status = self.wcf.is_login()
            if status:
                logger.info("登录成功！")
                break
            time.sleep(2)

        # 3. 获取登录信息
        user_info = self.wcf.get_user_info()
        logger.info("登录账号: wxid=%s, 昵称=%s", user_info.get("wxid"), user_info.get("name"))
        return True

    # ------------------------------------------------------------
    # 消息接收
    # ------------------------------------------------------------
    def enable_receiving(self) -> None:
        """开启消息接收并启动回调线程。"""
        self.wcf.enable_receiving_msg()
        logger.info("消息接收已开启")
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True, name="wcf-recv").start()

    def _recv_loop(self) -> None:
        """后台线程：轮询 wcferry 消息队列，分发给回调。"""
        while self._running:
            msg: WxMsg = self.wcf.get_msg()
            if msg is None:
                time.sleep(0.1)
                continue
            if self._callback:
                try:
                    self._callback(msg)
                except Exception:
                    logger.exception("消息回调异常")

    def on_message(self, callback: Callable[[WxMsg], None]) -> None:
        """注册消息回调函数。"""
        self._callback = callback

    # ------------------------------------------------------------
    # 消息发送
    # ------------------------------------------------------------
    def send_text(self, msg: str, receiver: str, aters: str = "") -> int:
        """
        发送文本消息（群聊/私聊通用）。
        - receiver: 群聊时为群 ID（roomid），私聊时为对方 wxid
        - aters: 要 @ 的人的 wxid，多个用逗号分隔
        返回 0 表示成功。
        """
        return self.wcf.send_text(msg, receiver, aters)

    def send_image(self, path: str, receiver: str) -> int:
        """发送图片。"""
        return self.wcf.send_image(path, receiver)

    # ------------------------------------------------------------
    # 联系人
    # ------------------------------------------------------------
    def get_contacts(self) -> list:
        """获取所有联系人。"""
        return self.wcf.get_contacts()

    # ------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------
    def stop(self) -> None:
        """停止接收并清理。"""
        self._running = False
        self.wcf.disable_receiving_msg()
        logger.info("WCF 已停止")
