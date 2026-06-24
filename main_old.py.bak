"""
群聊 AI 机器人 — WeFlow SSE 版
三层记忆体系：工作记忆 + 情景记忆 + 语义记忆
支持 tool use 联网搜索热梗
"""
import logging
import sys
import time

from src.config_loader import load_config
from src.weflow_client import WeFlowClient, WeFlowMessage
from src.bot_core import BotCore
from src.llm_client import LLMClient
from src.state import BotState
from src.user_memory import UserMemoryStore
from src.group_memory import GroupMemoryStore
from src.style_observer import StyleObserver
from src.proactive_speaker import ProactiveSpeaker
from src.context_builder import (
    build_llm_context,
    extract_facts_from_reply,
    extract_context_from_reply,
    auto_extract_facts,
)
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
    logger.info("Config: llm=%s/%s, bot=%s, search=%s",
                config.llm.provider, config.llm.model,
                config.bot.name, getattr(config.bot, 'enable_search', True))

    # 用户记忆（语义记忆）
    user_memory = UserMemoryStore(data_dir="data")
    logger.info("User memory: %d users loaded", user_memory.user_count)

    # 风格观察器
    style_observer = StyleObserver(max_buffer=30)
    logger.info("Style observer initialized (buffer=30)")

    # 群情景记忆（新增）
    group_memory = GroupMemoryStore(data_dir="data")
    logger.info("Group memory: %d memories loaded", group_memory.memory_count)

    state = BotState()
    set_bot_state(state)

    llm = LLMClient(config)
    client = WeFlowClient(access_token=config.weflow_token)
    client.set_bot_identity(nicknames=[config.bot.name], wxid="wxid_hgla5drf0k8119")
    # 同步联系人 → 让机器人认识群里所有人
    synced = client.sync_contacts_to_memory(user_memory)
    logger.info("Contacts synced: %d new members", synced)
    # 启动定时 name_cache 刷新（每 30 分钟），防止联系人过期
    client.start_periodic_sync(user_memory, interval_sec=1800)
    bot = BotCore(config, client, user_memory=user_memory,
                  style_observer=style_observer, data_dir="data")

    # ---- 启动时加载最近群消息到历史（静默，不触发回复） ----
    def _silent_load(msg: WeFlowMessage):
        """只记录消息到历史/统计/风格，不走 LLM 回复。"""
        bot.handle(msg)

    client.load_recent_messages(_silent_load, count=20)

    # 主动发言系统
    speaker = ProactiveSpeaker(config, llm, client, group_memory, user_memory)
    logger.info("Proactive speaker: %s", "enabled" if config.proactive.enabled else "disabled")

    # 记录群最后一条消息时间（冷场检测）
    last_msg_times: dict[str, float] = {}

    def on_msg(msg: WeFlowMessage):
        logger.debug("Msg: room=%s, sender=%s, text=%s",
                     msg.session_id, msg.sender_name, msg.content[:80])
        if not msg.is_group:
            return

        roomid = msg.roomid
        speaker_wxid = msg.sender_name

        # 更新最后消息时间（冷场检测用）
        last_msg_times[roomid] = time.time()

        # 所有消息都过 BotCore.handle（记录用户、观察风格）
        result = bot.handle(msg)

        # 非 @bot — 不做任何回复，直接返回
        if not client.is_at_bot(msg):
            return

        # 以下为 @bot 的正常回复流程
        if result is not None:
            reply, _ = result
            logger.info("Cmd: %s -> %s", roomid, reply[:50])
            client.send_text(reply, roomid, msg.sender_name)
            return

        # LLM 处理（@bot 且非命令）
        session = bot.get_session(roomid)

        # ---- 构建带完整上下文的消息 ----
        enriched_content = build_llm_context(
            msg=msg,
            session=session,
            user_memory=user_memory,
            group_memory=group_memory,
            bot_nicknames=client.bot_nicknames,
        )

        # 替换 session 中最后一条 user 消息的内容为增强版
        if session.history:
            last_msg = session.history[-1]
            if last_msg.role == "user" and last_msg.sender_wxid == speaker_wxid:
                last_msg.content = enriched_content

        logger.info("LLM: room=%s, user=%s, rounds=%d, mem=%d, topic=%s",
                    roomid, msg.display_name, len(session.history) // 2,
                    group_memory.memory_count if group_memory else 0,
                    (session.topic_summary or "")[:30])

        # ---- 调用 LLM（含 tool use 搜索） ----
        history_list = list(session.history)
        reply = llm.chat(history_list)

        # ---- 自动提取用户事实 ----
        auto_extract_facts(reply, speaker_wxid, msg.content, user_memory)

        # ---- 提取 /remember 指令 ----
        reply = extract_facts_from_reply(reply, speaker_wxid, user_memory)

        # ---- 提取 /context 指令 ----
        reply, context_update = extract_context_from_reply(reply)
        if context_update:
            bot.update_group_context(roomid, context_update)

        # ---- 记录回复到会话历史 ----
        bot.add_reply(roomid, reply)

        # ---- 提取回复中的 @mention，替换为真实名（内联 @） ----
        import re
        sender_display = client.get_display_name(msg.sender_name)
        inline_reply = reply

        # 匹配回复中的 @拉丁名（如 @B L U E）和 @中文名（2-4字）
        latin_ms = re.findall(r'@([a-zA-Z][a-zA-Z0-9 ]*(?:\s+[a-zA-Z][a-zA-Z0-9 ]*)*)', reply)
        cjk_ms = re.findall(r'@([一-鿿぀-ゟ가-힯]{2,4})', reply)
        all_names = list(set(latin_ms + cjk_ms))

        for name in all_names:
            name = name.strip()
            if not name:
                continue
            # 三层容错：find_by_name → find_contact → 丢弃
            real_name = None
            profile = user_memory.find_by_name(name)
            if profile:
                # 优先用 mention_name（基准名），其次是 preferred_name
                real_name = profile.get_mention_name() or profile.preferred_name or name
            else:
                wxid = client.find_contact(name)
                if wxid:
                    real_name = client.get_display_name(wxid)
                    logger.info("Mention resolved via contact cache: '%s' -> '%s'", name, real_name)
                else:
                    logger.warning("Unresolved mention discarded: '%s'", name)
                    continue

            # 替换文本中的 @LLM名 → @权威 mention 名（确保 UIA 选对人）
            if real_name and real_name != name:
                inline_reply = inline_reply.replace(f'@{name}', f'@{real_name}')

        # 去掉 LLM 可能在开头写的 @发送者（由 at_sender 自动处理，避免重复）
        if sender_display:
            inline_reply = re.sub(rf'^@?\s*{re.escape(sender_display)}\s*[,，]?\s*', '', inline_reply).strip()

        # ---- 发送（内联 @mention） ----
        client.send_text(inline_reply, roomid, sender_display)
        logger.info("Reply: @%s -> %s", sender_display, reply[:60])

        # ---- 定期更新话题摘要（工作记忆） ----
        if bot.should_update_topic(roomid):
            logger.info("触发话题摘要更新: room=%s", roomid[:20])
            try:
                recent = list(session.history)
                new_summary, new_keywords = llm.summarize_topic(
                    recent, session.topic_summary
                )
                if new_summary:
                    session.topic_summary = new_summary
                    session.topic_keywords = new_keywords
                    logger.info("话题摘要: %s | 关键词: %s",
                                new_summary[:60], new_keywords)
            except Exception:
                logger.exception("话题摘要失败: room=%s", roomid[:20])
            bot.reset_summary_counter(roomid)

        # ---- 定期提取情景记忆 ----
        if bot.should_extract_memory(roomid):
            logger.info("触发情景记忆提取: room=%s, msgs=%d",
                        roomid[:20], session.message_count)
            try:
                recent = list(session.history)
                new_mems = group_memory.consolidate(roomid, recent, llm)
                if new_mems:
                    logger.info("新情景记忆: %d 条", len(new_mems))
            except Exception:
                logger.exception("情景记忆提取失败: room=%s", roomid[:20])
            bot.reset_memory_counter(roomid)

        # ---- 定期更新群背景 ----
        if bot.should_summarize_context(roomid):
            logger.info("触发群上下文摘要: room=%s, msgs=%d",
                        roomid[:20], session.message_count)
            try:
                summary = llm.summarize_context(
                    history=list(session.history),
                    existing_context=session.group_context,
                )
                if summary:
                    bot.update_group_context(roomid, summary)
            except Exception:
                logger.exception("群上下文摘要失败: room=%s", roomid[:20])

        # ---- 定期风格分析 ----
        if bot.should_analyze_style(roomid):
            logger.info("触发风格分析: room=%s", roomid[:20])
            try:
                buf = style_observer.get_buffer(roomid)
                if buf:
                    result = llm.analyze_style(buf)

                    # 更新群风格
                    if result.get("group_style"):
                        bot.update_group_style(
                            roomid,
                            result["group_style"],
                            result.get("top_words", []),
                            result.get("top_emojis", []),
                        )
                        logger.info("群风格已更新: %s", result["group_style"][:60])

                    # 更新用户风格
                    for wxid, info in result.get("user_styles", {}).items():
                        user_memory.set_speaking_style(
                            wxid,
                            info.get("style", ""),
                            info.get("catchphrases", []),
                        )
                        if info.get("style"):
                            profile = user_memory.get(wxid)
                            name = profile.preferred_name if profile else wxid
                            logger.info("用户风格已更新: %s -> %s", name, info["style"][:40])
            except Exception:
                logger.exception("风格分析失败: room=%s", roomid[:20])
            style_observer.reset_buffer(roomid)

    client.on_message(on_msg)
    client.start_receiving()
    start_web(8766)
    state.running = True

    # ---- 主动发言后台线程 ----
    def _proactive_loop():
        """每分钟检查一次主动发言条件。"""
        time.sleep(10)  # 启动后先等 10 秒
        while state.running:
            try:
                for room_id in list(bot._sessions.keys()):
                    session = bot.get_session(room_id)
                    last_time = last_msg_times.get(room_id, 0.0)
                    speaker.check_and_speak(room_id, session, last_time)
            except Exception:
                logger.exception("proactive loop error")
            time.sleep(60)

    import threading
    threading.Thread(target=_proactive_loop, daemon=True, name="proactive").start()
    logger.info("Proactive loop started")

    # ---- 启动暖场（延时 90s） ----
    def _startup_warmup():
        time.sleep(90)
        for room_id in list(bot._sessions.keys()):
            session = bot.get_session(room_id)
            last_time = last_msg_times.get(room_id, 0.0)
            speaker.on_startup(room_id, session, last_time)

    threading.Thread(target=_startup_warmup, daemon=True).start()

    logger.info("=" * 50)
    logger.info("Bot started (WeFlow + DeepSeek + 3-tier Memory + Search + Style Learning)")
    logger.info("  Proactive: %s (cold=%dmin, max=%d/day)",
                "on" if config.proactive.enabled else "off",
                config.proactive.cold_silence_minutes,
                config.proactive.max_per_day)
    logger.info("  Web:    http://127.0.0.1:8766")
    logger.info("  Memory: %d users, %d group memories",
                user_memory.user_count, group_memory.memory_count)
    logger.info("  Search: %s", "enabled" if getattr(config.bot, 'enable_search', True) else "disabled")
    logger.info("  Ctrl+C to exit")
    logger.info("=" * 50)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        state.running = False
        user_memory.save()
        group_memory.save()
        logger.info("Memory saved: %d users, %d group memories",
                    user_memory.user_count, group_memory.memory_count)
        client.stop()


if __name__ == "__main__":
    main()
