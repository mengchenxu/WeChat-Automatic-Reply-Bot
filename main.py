"""
群聊 AI 机器人 — 入口（纯 UIA 方案）
├─ UIA 轮询接收微信消息
├─ BotCore 过滤/路由/会话管理
├─ LLMClient DeepSeek 回复
└─ UIA 自动化发送回复

用法: python main.py
前提: 微信 4.x 已登录小号，窗口可见（不要最小化到托盘）
"""
import logging
import sys
import time

from src.config_loader import load_config
from src.wechat_client import WeChatClient, WeChatMessage
from src.bot_core import BotCore
from src.llm_client import LLMClient
from src.state import BotState
from src.web_panel import start_web, set_bot_state


def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG)

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
    logger.info("配置: llm=%s/%s, bot=%s", config.llm.provider, config.llm.model, config.bot.name)

    # 2. 全局状态
    state = BotState()
    set_bot_state(state)

    # 3. 初始化模块
    llm = LLMClient(config)
    client = WeChatClient(poll_interval=1.0)
    client.set_bot_identity(
        nicknames=[config.bot.name],
        wxid=getattr(config, 'bot_wxid', ''),
    )
    bot = BotCore(config, client)

    # 4. 消息回调
    def on_msg(msg: WeChatMessage):
        logger.debug(
            "消息: session=%s, sender=%s, content=%s",
            msg.session_name, msg.sender_name, msg.content[:80],
        )

        # 命令处理
        cmd_result = bot.handle(msg)
        if cmd_result is not None:
            reply_text, roomid = cmd_result
            logger.info("命令: roomid=%s, reply=%s", roomid, reply_text[:50])
            client.send_text(reply_text, roomid, msg.sender_name)
            return

        # LLM 处理
        if msg.is_group and client.is_at_bot(msg):
            roomid = msg.roomid
            history = bot.get_history(roomid)
            logger.info("LLM: roomid=%s, rounds=%d", roomid, len(history)//2)

            reply = llm.chat(history)
            bot.add_reply(roomid, reply)

            client.send_text(reply, roomid, msg.sender_name)
            logger.info("回复: roomid=%s, text=%s", roomid, reply[:50])

    client.on_message(on_msg)
    client.start_receiving()

    # 5. Web 面板
    start_web(8766)
    state.running = True

    logger.info("=" * 50)
    logger.info("✅ 机器人已启动（纯 UIA 模式）")
    logger.info("   Web 面板: http://127.0.0.1:8766")
    logger.info("   请确保微信窗口可见（不要最小化到托盘）")
    logger.info("   在群里 @%s 即可对话", config.bot.name)
    logger.info("   按 Ctrl+C 退出")
    logger.info("=" * 50)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到退出信号")
    finally:
        state.running = False
        client.stop()


if __name__ == "__main__":
    main()
