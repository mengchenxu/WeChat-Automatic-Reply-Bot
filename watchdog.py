"""
Watchdog — 守护进程，监控 bot 进程并在崩溃时自动重启。

用法:
    python watchdog.py           # 前台运行
    python watchdog.py --daemon  # 后台运行（配合 nssm）

工作原理：
    1. 启动 bot 子进程 (python main.py)
    2. 监控子进程状态
    3. 崩溃后等待 5 秒，自动重启
    4. 连续崩溃 >=5 次且间隔 <60s 则放弃，避免死循环
"""
import subprocess
import sys
import time
import logging

logger = logging.getLogger("watchdog")

MAX_CRASH_COUNT = 5
CRASH_WINDOW_SEC = 60
RESTART_DELAY_SEC = 5


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [WATCHDOG] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    crash_times = []
    restart_count = 0

    while True:
        logger.info("启动 bot 进程 (第 %d 次)...", restart_count + 1)
        proc = subprocess.Popen([sys.executable, "main.py"])

        # 等待进程退出
        proc.wait()
        exit_code = proc.returncode
        now = time.time()

        logger.warning("bot 进程退出, exit_code=%d", exit_code)

        # 滑动窗口统计崩溃次数
        crash_times.append(now)
        crash_times = [t for t in crash_times if now - t < CRASH_WINDOW_SEC]

        if len(crash_times) >= MAX_CRASH_COUNT:
            logger.error(
                "%d 秒内崩溃 %d 次，退出 watchdog",
                CRASH_WINDOW_SEC, len(crash_times),
            )
            sys.exit(1)

        restart_count += 1
        logger.info("%d 秒后重启...", RESTART_DELAY_SEC)
        time.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    main()
