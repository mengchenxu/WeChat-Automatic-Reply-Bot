"""
群聊 AI 机器人 — 入口
用法: python main.py
"""
import logging
import sys
import time

from wcferry import WxMsg

from src.config_loader import load_config
from src.wcf_client import WcfClient
from src.bot_core import BotCore
from src.llm_client import LLMClient


def setup_logging():
    """配置日志：控制台 + 文件（按天轮转）。"""
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG)

    # 文件
    from logging.handlers import TimedRotatingFileHandler
    file_handler = TimedRotatingFileHandler(
        "logs/bot.log", when="midnight", backupCount=7, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)


def main():
    setup_logging()
    logger = logging.getLogger("main")

    # 1. 加载配置
    config = load_config()
    logger.info("配置加载完成: llm.provider=%s, llm.model=%s", config.llm.provider, config.llm.model)

    # 2. 初始化各模块
    wcf = WcfClient()
    wcf.login()

    llm = LLMClient(config)
    bot = BotCore(config, wcf)

    # 3. 注册消息回调
    def on_msg(msg: WxMsg):
        logger.debug(
            "消息: type=%s, sender=%s, roomid=%s, content=%s",
            msg.type, msg.sender, msg.roomid, msg.content,
        )

        # BotCore 处理：过滤 + 命令
        cmd_result = bot.handle(msg)
        if cmd_result is not None:
            reply_text, roomid = cmd_result
            logger.info("命令回复: roomid=%s, reply=%s", roomid, reply_text[:50])
            wcf.send_text(reply_text, roomid, msg.sender)
            return

        # 需要 LLM 处理的消息
        if msg.from_group() and bot._is_at_bot(msg):
            roomid = msg.roomid
            history = bot.get_history(roomid)
            logger.info("LLM 请求: roomid=%s, history_rounds=%d", roomid, len(history) // 2)

            reply = llm.chat(history)
            bot.add_reply(roomid, reply)

            wcf.send_text(reply, roomid, msg.sender)
            logger.info("LLM 回复: roomid=%s, reply=%s", roomid, reply[:50])

    wcf.on_message(on_msg)
    wcf.enable_receiving()

    logger.info("✅ 机器人已启动，按 Ctrl+C 退出")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    finally:
        wcf.stop()


if __name__ == "__main__":
    main()
