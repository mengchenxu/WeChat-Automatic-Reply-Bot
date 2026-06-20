"""
主动发言系统 — 冷场暖场 / 定时推送 / 热点分享。
后台线程每分钟检查一次触发条件。
"""
import logging
import random
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class ProactiveSpeaker:
    """主动发言控制器"""

    def __init__(self, config, llm_client, weflow_client, group_memory, user_memory):
        llm = config.llm
        proactive = config.proactive

        self.enabled = proactive.enabled
        self.cold_silence_minutes = proactive.cold_silence_minutes
        self.schedule_times = proactive.schedule_times
        self.hot_topic_interval_hours = proactive.hot_topic_interval_hours
        self.max_per_day = proactive.max_per_day
        self.min_interval_minutes = proactive.min_interval_minutes
        self.quiet_hours = proactive.quiet_hours  # ["02:00", "06:00"]

        self.llm = llm_client
        self.weflow = weflow_client
        self.group_memory = group_memory
        self.user_memory = user_memory

        # 内部状态
        self._sent_today = 0
        self._last_sent_at: float = 0.0
        self._last_hot_check_at: float = 0.0
        self._day_reset_at: str = ""  # 日期，用于每日重置

    # ----------------------------------------------------------------
    # 公共方法
    # ----------------------------------------------------------------
    def check_and_speak(self, room_id: str, session, last_msg_time: float) -> bool:
        """
        检查所有触发条件，满足则发言。返回 True 表示发了言。
        在后台线程中每分钟调用一次。
        """
        if not self.enabled:
            return False

        # 每日重置
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._day_reset_at:
            self._sent_today = 0
            self._day_reset_at = today

        # 上限检查
        if self._sent_today >= self.max_per_day:
            return False

        # 静音时段
        if self.is_quiet_hours():
            return False

        # 最小间隔
        if self._last_sent_at > 0:
            elapsed_min = (time.time() - self._last_sent_at) / 60
            if elapsed_min < self.min_interval_minutes:
                return False

        # 判断触发原因
        reason = self._get_trigger_reason(room_id, last_msg_time)
        if not reason:
            return False

        # 生成话题并发送
        try:
            topic = self._generate_topic(room_id, reason, session)
            if topic:
                self.weflow.send_text(topic, room_id)  # 不 @ 任何人
                self.record_sent()
                logger.info("主动发言 [%s]: room=%s, topic=%s",
                            reason, room_id[:20], topic[:60])
                return True
        except Exception:
            logger.exception("主动发言失败: room=%s", room_id[:20])
        return False

    def on_startup(self, room_id: str, session, last_msg_time: float):
        """启动时检查冷场时长，如果超阈值则等待后发言。"""
        if not self.enabled or self.is_quiet_hours():
            return
        if self._sent_today >= self.max_per_day:
            return

        silence_min = (time.time() - last_msg_time) / 60 if last_msg_time > 0 else float('inf')
        if silence_min >= self.cold_silence_minutes:
            logger.info("启动冷场检测: room=%s, silence=%.0fmin, 等待90s后发言",
                        room_id[:20], silence_min)
            # 等待 90 秒让 WeFlow 连接稳定后发言
            time.sleep(90)
            try:
                session_obj = session
                topic = self._generate_topic(room_id, "cold_silence", session_obj)
                if topic:
                    self.weflow.send_text(topic, room_id)
                    self.record_sent()
                    logger.info("启动暖场: room=%s, topic=%s",
                                room_id[:20], topic[:60])
            except Exception:
                logger.exception("启动暖场失败: room=%s", room_id[:20])

    def record_sent(self):
        """记录一次发言，更新计数和时间。"""
        self._sent_today += 1
        self._last_sent_at = time.time()

    def is_quiet_hours(self) -> bool:
        """判断当前是否在静音时段。"""
        if not self.quiet_hours or len(self.quiet_hours) < 2:
            return False
        now = datetime.now().strftime("%H:%M")
        start = self.quiet_hours[0]   # e.g. "02:00"
        end = self.quiet_hours[1]     # e.g. "06:00"
        if start <= end:
            return start <= now < end
        else:
            # 跨午夜的情况，如 22:00-08:00
            return now >= start or now < end

    # ----------------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------------
    def _get_trigger_reason(self, room_id: str, last_msg_time: float) -> str | None:
        """
        判断触发原因。优先级：定时 > 冷场 > 热点。
        返回 "scheduled" | "cold_silence" | "hot_topic" | None
        """
        now = datetime.now()
        now_str = now.strftime("%H:%M")

        # 1. 定时推送（±2 分钟内）
        for t in self.schedule_times:
            h1, m1 = int(now_str[:2]), int(now_str[3:5])
            h2, m2 = int(t[:2]), int(t[3:5])
            diff = abs((h1 * 60 + m1) - (h2 * 60 + m2))
            if diff <= 2:
                # 该时段今天还没发过
                return "scheduled"

        # 2. 冷场检测
        if last_msg_time > 0:
            silence_min = (time.time() - last_msg_time) / 60
            if silence_min >= self.cold_silence_minutes:
                return "cold_silence"
        else:
            # 没有 last_msg_time 记录，当做冷场
            return "cold_silence"

        # 3. 热点检测
        if self._last_hot_check_at == 0:
            self._last_hot_check_at = time.time()
        hot_elapsed = (time.time() - self._last_hot_check_at) / 3600
        if hot_elapsed >= self.hot_topic_interval_hours:
            self._last_hot_check_at = time.time()
            return "hot_topic"

        return None

    def _generate_topic(self, room_id: str, reason: str, session) -> str:
        """LLM 生成话题文案。"""
        # 检索上下文
        topic_keywords = getattr(session, 'topic_keywords', []) or []
        memories = []
        if self.group_memory and topic_keywords:
            memories = self.group_memory.search(room_id, topic_keywords, limit=3)

        # 检索用户偏好
        active_users = getattr(session, 'active_users', set()) or set()
        user_ctx = ""
        if self.user_memory and active_users:
            profiles = []
            for wxid in list(active_users)[:5]:
                p = self.user_memory.get(wxid)
                if p and p.get_context_summary():
                    profiles.append(p.get_context_summary())
            if profiles:
                user_ctx = "群成员:\n" + "\n".join(profiles)

        # 群风格
        group_style = getattr(session, 'group_style', '') or ''

        # 热点搜索
        hot_content = ""
        if reason == "hot_topic":
            try:
                from src.web_search import search_web, search_format_for_llm
                results = search_web("今日热点新闻")
                if results:
                    hot_content = "热点新闻:\n" + search_format_for_llm(results[:3])
            except Exception:
                pass

        # 构建 prompt
        reason_text = {
            "cold_silence": "群里已经冷场很久了，抛个话题暖暖场",
            "scheduled": "到点了，发个日常闲聊/问候",
            "hot_topic": "分享一下最近的热点，引发讨论",
        }.get(reason, "自然地说点什么")

        prompt = f"""你是微信群里的"鼠鼠"。现在需要你主动说句话。

原因: {reason_text}

{hot_content}
群风格: {group_style}
群记忆:
{chr(10).join(f'  · {m.content}' for m in memories) if memories else '  暂无'}
{user_ctx}

请生成 1-2 句话，发给群里。要求：
- 自然不突兀，像真人聊天，不要说自己"我来活跃气氛"之类的话
- 保持群的说话风格
- 如果有群记忆中没聊完的话题，优先续那个
- 不要 @ 任何人，是群发
- 直接返回发言内容，不要前缀"""

        resp = self.llm.client.chat.completions.create(
            model=self.llm.model,
            messages=[
                {"role": "system", "content": "你是一个群聊成员，自然地发起话题。只返回要发的消息内容。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temperature=0.8,  # 稍高温度，话题多样化
        )
        return resp.choices[0].message.content.strip()
