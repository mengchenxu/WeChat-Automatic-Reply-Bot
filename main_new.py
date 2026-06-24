"""群聊 AI 机器人 — 管道架构版"""
import logging
import sys
import time

from src.config import load_config
from src.weflow_client import WeFlowClient  # 临时：Issue 6 迁移到 src/weflow.py
from src.pipeline import Pipeline


def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG)

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

    # WeFlow client（Issue 6 迁移到 src/weflow.py）
    client = WeFlowClient(access_token=config.weflow_token)
    client.set_bot_identity(nicknames=[config.bot.name])

    pipeline = Pipeline(config, client)
    pipeline.start()

    def on_msg(msg_data: dict):
        pipeline.handle(msg_data)

    client.on_message(on_msg)
    client.start_receiving()

    logger.info("=" * 40)
    logger.info("Bot started: %s (model=%s)", config.bot.name, config.llm.model)
    logger.info("Ctrl+C to exit")
    logger.info("=" * 40)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        pipeline.stop()
        client.stop()


if __name__ == "__main__":
    main()
