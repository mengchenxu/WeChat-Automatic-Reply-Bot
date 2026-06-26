"""管道编排 — 主循环：Parse → Enrich → Prompt → LLM → Decode → Send"""
import logging
import time
from typing import Optional

from src.config import AppConfig
from src.store import Store, ChatMsg
from src.parse import parse, ParsedMsg
from src.enrich import enrich
from src.prompt import build_prompt
from src.llm import LLMClient
from src.decode import decode, DecodedReply
from src.send import send
from src.proactive import ProactiveSpeaker
from src.game_intent import GameIntentDetector

logger = logging.getLogger(__name__)

# 摘要更新间隔（消息数）
_SUMMARY_INTERVAL = 15
# 记忆提取间隔（消息数）
_EXTRACT_INTERVAL = 10
# 姓名缓存刷新间隔（秒）
_NAME_SYNC_INTERVAL = 1800  # 30 min
# 启动历史加载条数
_STARTUP_HISTORY_COUNT = 20


class Pipeline:
    """六个阶段的编排器。持有 Store 和 LLMClient。"""

    def __init__(self, config: AppConfig, weflow_client):
        self.config = config
        self.weflow = weflow_client
        self.store = Store.load()
        self.llm = LLMClient(config)
        self.bot_names = [config.bot.name]
        self.cooldown = config.bot.reply_cooldown_seconds
        self._last_sync = 0.0
        self._running = False

        # 主动发言后台线程
        self.proactive = ProactiveSpeaker(
            store=self.store,
            llm=self.llm,
            config=config,
            send_fn=send,
            weflow_client=weflow_client,
        )

        # 游戏意图检测
        self.game_detector = GameIntentDetector(config)

    # ================================================================
    # 生命周期
    # ================================================================
    def start(self):
        """启动管道：同步联系人 → 加载历史 → 开始轮询。"""
        logger.info("Pipeline starting...")

        # 同步联系人
        try:
            self.weflow.sync_contacts_to_memory(self.store)
        except Exception:
            logger.exception("Initial sync failed, continuing...")

        # 启动定时 name_cache 刷新
        self._last_sync = time.time()

        # 加载最近消息到历史
        try:
            self._load_recent_history()
        except Exception:
            logger.exception("History loading failed, continuing...")

        self._running = True
        self.proactive.start()
        logger.info("Pipeline started. Store: %d people, %d groups",
                    len(self.store._people), len(self.store._groups))

    def stop(self):
        """停止管道，保存数据。"""
        self._running = False
        self.proactive.stop()
        try:
            self.store.save()
            logger.info("Store saved.")
        except Exception:
            logger.exception("Save failed on shutdown")

    # ================================================================
    # 消息处理入口
    # ================================================================
    def handle(self, msg_data: dict) -> Optional[str]:
        """处理一条 WeFlow 消息。返回 bot 回复文本或 None。"""
        if not self._running:
            return None

        # Phase 1: Parse（传入 bot wxid 用于过滤自己的消息）
        bot_wxid = self.weflow.bot_wxid if self.weflow.bot_wxid else ""
        parsed = parse(msg_data, self.bot_names, bot_wxids=[bot_wxid] if bot_wxid else [])
        if parsed is None:
            return None  # 私聊，跳过

        # 记录到历史 + 实时风格统计（@和非@ 都做）
        self._add_to_history(parsed)
        self.store.track_style(parsed.room_id, parsed.content)

        # Phase 2: Enrich（非@ 在此返回）
        enriched = enrich(parsed, self.store, self.bot_names)
        if enriched is None:
            # 游戏意图检测（非@消息）
            game_result = self.game_detector.detect(parsed)
            if game_result == "想玩":
                game_list = self.game_detector.get_game_list()
                # 记录回复到历史
                self.store.add_to_history(parsed.room_id, ChatMsg(
                    role="assistant", content=game_list,
                    sender_name=self.config.bot.name,
                    timestamp=time.time(),
                ))
                # 发送游戏列表
                try:
                    sender_display = self.weflow.get_display_name(parsed.sender_wxid) or parsed.sender_name
                    send(DecodedReply(clean_text=game_list), parsed.room_id, sender_display)
                except Exception:
                    logger.exception("Game list send failed")

                self._check_summary(parsed.room_id)
                self._check_extract(parsed.room_id)
                self._check_name_sync()
                return game_list

            self._check_summary(parsed.room_id)
            self._check_extract(parsed.room_id)
            self._check_name_sync()
            return None

        # 游戏命令直接处理（不走 LLM）
        if parsed.is_command and parsed.command == "/骰子":
            return self._handle_dice(parsed)

        # Phase 3-6: 完整管道（仅 @bot）
        # 冷却检查（基于 bot 上次回复时间，非最后消息时间）
        group = self.store.get_group(parsed.room_id)
        elapsed = time.time() - group.last_reply_at
        if elapsed < self.cooldown:
            logger.debug("Cooling down: %.1fs < %ds", elapsed, self.cooldown)
            return None
        group.last_reply_at = time.time()

        # Phase 3: Prompt
        messages = build_prompt(enriched, self.config.bot.system_prompt)

        # Phase 4: LLM
        reply = self.llm.chat(messages, tools_enabled=self.config.enable_search)

        # Phase 5: Decode
        decoded = decode(reply, enriched, self.store)

        # 应用 Store 变更
        if decoded.mutations:
            self.store.apply_mutations(decoded.mutations)

        # Phase 6: Send
        sender_display = self.weflow.get_display_name(parsed.sender_wxid) or parsed.sender_name
        send(decoded, parsed.room_id, sender_display)

        # 记录回复到历史
        self.store.add_to_history(parsed.room_id, ChatMsg(
            role="assistant", content=decoded.clean_text,
            sender_name=self.config.bot.name,
            timestamp=time.time(),
        ))

        self._check_summary(parsed.room_id)
        self._check_extract(parsed.room_id)
        self._check_name_sync()

        return decoded.clean_text

    def _handle_dice(self, parsed: ParsedMsg) -> str:
        """处理 /骰子 命令：随机 1-6。"""
        import random
        result = random.randint(1, 6)
        reply = f"🎲 人家摇出了 [{result}] 喵~"
        # 记录回复到历史
        self.store.add_to_history(parsed.room_id, ChatMsg(
            role="assistant", content=reply,
            sender_name=self.config.bot.name,
            timestamp=time.time(),
        ))
        # 发送
        sender_display = self.weflow.get_display_name(parsed.sender_wxid) or parsed.sender_name
        send(DecodedReply(clean_text=reply), parsed.room_id, sender_display)
        return reply

    # ================================================================
    # 内部方法
    # ================================================================
    def _add_to_history(self, parsed: ParsedMsg):
        """添加消息到群历史（所有消息）。"""
        self.store.add_to_history(parsed.room_id, ChatMsg(
            role="user", content=parsed.content,
            sender_name=parsed.sender_name, sender_wxid=parsed.sender_wxid,
            timestamp=time.time(),
        ))

    def _check_summary(self, room_id: str):
        """检查是否应该触发群聊摘要更新。"""
        g = self.store.get_group(room_id)
        if g.msg_count > 0 and g.msg_count % _SUMMARY_INTERVAL == 0:
            try:
                history = self.store.get_history(room_id, limit=15)
                if len(history) >= 6:
                    summary = self.llm.summarize_context(history, g.context)
                    if summary:
                        self.store.update_summary(room_id, summary)
                        logger.info("Summary updated: room=%s", room_id[:20])
            except Exception:
                logger.exception("Summary update failed: room=%s", room_id[:20])

    def _check_extract(self, room_id: str):
        """每 N 条消息触发一次记忆提取。"""
        g = self.store.get_group(room_id)
        if g.msg_count > 0 and g.msg_count % _EXTRACT_INTERVAL == 0:
            try:
                history = self.store.get_history(room_id, limit=15)
                if len(history) < 5:
                    return
                items = self.llm.extract_memories(history)
                if not items:
                    return

                added = 0
                facts_added = 0
                for item in items:
                    content = item.get("content", "").strip()
                    if not content:
                        continue
                    keywords = item.get("keywords", [])
                    category = item.get("category", "fact")
                    importance = min(5, max(1, item.get("importance", 3)))
                    participants = item.get("participants", [])

                    # 写入群记忆
                    self.store.add_memory(room_id, content, keywords=keywords,
                                          category=category, importance=importance)
                    added += 1

                    # 提取原子 facts（LLM 在 item["facts"] 中提供，最多 2 条）
                    for fact in item.get("facts", [])[:2]:
                        if facts_added >= 2:
                            break
                        name = fact.get("person", "")
                        key = fact.get("key", "")
                        value = fact.get("value", "")
                        if not name or not key or not value:
                            continue
                        person, _ = self.store.resolve_name(name)
                        if person and person.add_fact(key, value, source="llm_extract", confidence=0.6):
                            facts_added += 1

                # 提取人际关系
                for item in items:
                    for rel in item.get("relations", []):
                        person_name = rel.get("person", "")
                        target_name = rel.get("target", "")
                        label = rel.get("label", "")
                        if not person_name or not target_name or not label:
                            continue
                        p, _ = self.store.resolve_name(person_name)
                        t, _ = self.store.resolve_name(target_name)
                        if p and t:
                            p.relations[t.wxid] = label
                            t.relations[p.wxid] = label

                # 定期清理低价值旧记忆
                self.store.cleanup_old_memories(room_id)

                if added:
                    logger.info("Memory extraction: %d memories, %d facts (room=%s)",
                                added, facts_added, room_id[:20])
            except Exception:
                logger.exception("Memory extraction failed: room=%s", room_id[:20])

    def _check_name_sync(self):
        """定时刷新 name_cache。"""
        now = time.time()
        if now - self._last_sync >= _NAME_SYNC_INTERVAL:
            try:
                self.weflow._build_name_cache()
                self.weflow.sync_contacts_to_memory(self.store)
                self._last_sync = now
                logger.info("Periodic name sync complete")
            except Exception:
                logger.exception("Name sync failed")

    def _load_recent_history(self):
        """启动时加载最近群消息到历史。"""
        sessions = self.weflow._api_get("/api/v1/sessions")
        if not sessions:
            return
        total = 0
        for sess in sessions.get("sessions", []):
            talker = sess.get("username", "")
            if "@chatroom" not in talker:
                continue
            resp = self.weflow._api_get(f"/api/v1/messages?talker={talker}&media=false")
            if not resp:
                continue
            messages = resp.get("messages", [])
            recent = sorted(messages, key=lambda m: m.get("createTime", 0))[-_STARTUP_HISTORY_COUNT:]
            for mdata in recent:
                content = (mdata.get("content", "") or "").strip()
                if not content:
                    continue
                sender = mdata.get("senderUsername", "")
                if sender == self.weflow.bot_wxid or sender in self.bot_names:
                    continue
                self.store.add_to_history(talker, ChatMsg(
                    role="user", content=content, sender_name=sender,
                    sender_wxid=sender, timestamp=mdata.get("createTime", 0),
                ))
                total += 1
            logger.info("Loaded %d recent messages for room=%s", len(recent), talker[:20])
        if total:
            logger.info("Startup history loaded: %d messages total", total)
