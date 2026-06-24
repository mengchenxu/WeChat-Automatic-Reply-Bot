"""管道编排 — 主循环：Parse → Enrich → Prompt → LLM → Decode → Send"""
import logging
import time
from typing import Dict, List, Optional

from src.config import AppConfig
from src.store import Store, ChatMsg
from src.parse import parse, ParsedMsg
from src.enrich import enrich
from src.prompt import build_prompt
from src.llm import LLMClient
from src.decode import decode
from src.send import send

logger = logging.getLogger(__name__)

# 摘要更新间隔（消息数）
_SUMMARY_INTERVAL = 15
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
        logger.info("Pipeline started. Store: %d people, %d groups",
                    len(self.store._people), len(self.store._groups))

    def stop(self):
        """停止管道，保存数据。"""
        self._running = False
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

        # Phase 1: Parse
        parsed = parse(msg_data, self.bot_names)
        if parsed is None:
            return None  # 私聊，跳过

        # 记录到历史（@和非@ 都记录）
        self._add_to_history(parsed)

        # Phase 2: Enrich（非@ 在此返回）
        enriched = enrich(parsed, self.store, self.bot_names)
        if enriched is None:
            self._check_summary(parsed.room_id)
            self._check_name_sync()
            return None

        # Phase 3-6: 完整管道（仅 @bot）
        # 冷却检查
        group = self.store.get_group(parsed.room_id)
        elapsed = time.time() - group.last_msg_at
        if elapsed < self.cooldown:
            logger.debug("Cooling down: %.1fs < %ds", elapsed, self.cooldown)
            return None

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
        self._check_name_sync()

        return decoded.clean_text

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


    def summarize_context(self, history: list, existing: str = "") -> str:
        """LLM 群聊摘要（桥接到 LLMClient.summarize_context）。"""
        return self.llm.summarize_context(history, existing)
