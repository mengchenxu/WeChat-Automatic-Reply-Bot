"""
群聊 AI 机器人 — WeFlow SSE 版
WeFlow SSE 收 → BotCore → DeepSeek → UIA 发
"""
import logging
import sys
import time

from src.config_loader import load_config
from src.weflow_client import WeFlowClient, WeFlowMessage
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
    logging.getLogger("comtypes").setLevel(logging.WARNING)

    from logging.handlers import TimedRotatingFileHandler
    fh = TimedRotatingFileHandler("logs/bot.log", when="midnight", backupCount=7, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(fh)


def main():
    setup_logging()
    logger = logging.getLogger("main")

    config = load_config()
    logger.info("Config: llm=%s/%s, bot=%s", config.llm.provider, config.llm.model, config.bot.name)

    state = BotState()
    set_bot_state(state)

    llm = LLMClient(config)
    client = WeFlowClient(access_token=config.weflow_token)
    client.set_bot_identity(nicknames=[config.bot.name])
    bot = BotCore(config, client)

    def on_msg(msg: WeFlowMessage):
        logger.debug("Msg: room=%s, sender=%s, text=%s", msg.session_id, msg.sender_name, msg.content[:80])
        if not msg.is_group:
            return

        result = bot.handle(msg)
        if result is not None:
            reply, roomid = result
            logger.info("Cmd: %s -> %s", roomid, reply[:50])
            client.send_text(reply, roomid, msg.sender_name)
            return

        if client.is_at_bot(msg):
            roomid = msg.roomid
            history = bot.get_history(roomid)
            logger.info("LLM: room=%s, rounds=%d", roomid, len(history)//2)

            reply = llm.chat(history)
            bot.add_reply(roomid, reply)
            client.send_text(reply, roomid, msg.sender_name)
            logger.info("Reply: %s -> %s", roomid, reply[:50])

    client.on_message(on_msg)
    client.start_receiving()
    start_web(8766)
    state.running = True

    logger.info("=" * 50)
    logger.info("Bot started (WeFlow SSE + DeepSeek + UIA)")
    logger.info("  Web: http://127.0.0.1:8766")
    logger.info("  Ctrl+C to exit")
    logger.info("=" * 50)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        state.running = False
        client.stop()


if __name__ == "__main__":
    main()
