"""
用户记忆系统 — 识别并记忆群聊中每个用户的身份、特征、对话历史。
持久化到 data/users.json，跨重启保留。
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    """群成员档案"""
    wxid: str
    mention_name: str = ""                                     # 基准 @mention 名（联系人 API 写入，永不覆盖）
    display_names: List[str] = field(default_factory=list)   # 历史显示名
    preferred_name: str = ""                                   # 消息中出现的名字（可能变化）
    first_seen: float = 0.0
    last_seen: float = 0.0
    message_count: int = 0
    known_facts: Dict[str, str] = field(default_factory=dict)  # {"职业": "程序员", "喜欢": "猫"}
    relations: Dict[str, str] = field(default_factory=dict)     # {"wxid_xxx": "同事", "wxid_yyy": "朋友"}
    topics: List[str] = field(default_factory=list)             # 常讨论的话题
    notes: str = ""                                              # LLM 可更新的自由格式备注
    aliases: List[str] = field(default_factory=list)            # 外号/别名（自动学习）

    # 风格学习字段
    speaking_style: str = ""            # LLM 生成的个人风格描述（1句话）
    catchphrases: list = field(default_factory=list)  # 口头禅

    def get_mention_name(self) -> str:
        """返回可用于 @mention 的权威名字。优先 mention_name，其次 preferred_name，最后 wxid。"""
        name = self.mention_name or self.preferred_name or self.wxid
        # 如果解析出来是 wxid，说明没有人类可读的名字，返回空让调用方处理
        if name.startswith("wxid_") or name.startswith("wxid-"):
            return ""
        return name

    def update_name(self, name: str):
        """追踪显示名变化"""
        if name and name not in self.display_names:
            self.display_names.append(name)
            # 只保留最近 5 个
            if len(self.display_names) > 5:
                self.display_names = self.display_names[-5:]
        if name:
            self.preferred_name = name

    def record_activity(self):
        """记录一次发言"""
        now = time.time()
        if not self.first_seen:
            self.first_seen = now
        self.last_seen = now
        self.message_count += 1

    def set_fact(self, key: str, value: str):
        """设置一个已知事实"""
        self.known_facts[key] = value

    def get_context_summary(self) -> str:
        """生成 LLM 可用的用户摘要"""
        parts = []
        display = self.mention_name or self.preferred_name
        if display:
            parts.append(f"名字: {display}")
        if self.known_facts:
            facts = ", ".join(f"{k}={v}" for k, v in self.known_facts.items())
            parts.append(f"已知: {facts}")
        if self.relations:
            rel_text = ", ".join(f"与{rid}关系={rel}" for rid, rel in self.relations.items())
            parts.append(f"关系: {rel_text}")
        if self.topics:
            parts.append(f"常聊: {', '.join(self.topics[-5:])}")
        if self.notes:
            parts.append(f"备注: {self.notes}")
        if self.speaking_style:
            parts.append(f"风格: {self.speaking_style}")
        if not parts:
            return ""
        return " | ".join(parts)


class UserMemoryStore:
    """
    用户记忆存储，管理所有遇到过的人。
    持久化到 data/users.json。
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.file_path = os.path.join(data_dir, "users.json")
        self._users: Dict[str, UserProfile] = {}
        os.makedirs(data_dir, exist_ok=True)
        self.load()

    # ----------------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------------
    def load(self):
        """从 JSON 文件加载所有用户档案"""
        if not os.path.exists(self.file_path):
            logger.info("用户记忆文件不存在，从空开始: %s", self.file_path)
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for wxid, d in data.items():
                profile = UserProfile(
                    wxid=wxid,
                    mention_name=d.get("mention_name", ""),
                    display_names=d.get("display_names", []),
                    preferred_name=d.get("preferred_name", ""),
                    first_seen=d.get("first_seen", 0.0),
                    last_seen=d.get("last_seen", 0.0),
                    message_count=d.get("message_count", 0),
                    known_facts=d.get("known_facts", {}),
                    relations=d.get("relations", {}),
                    topics=d.get("topics", []),
                    notes=d.get("notes", ""),
                    speaking_style=d.get("speaking_style", ""),
                    catchphrases=d.get("catchphrases", []),
                    aliases=d.get("aliases", []),
                )
                self._users[wxid] = profile
            logger.info("已加载 %d 个用户档案", len(self._users))
        except Exception:
            logger.exception("加载用户记忆失败，从空开始")

    def save(self):
        """保存所有用户档案到 JSON 文件"""
        try:
            data = {}
            for wxid, profile in self._users.items():
                data[wxid] = {
                    "wxid": profile.wxid,
                    "mention_name": profile.mention_name,
                    "display_names": profile.display_names,
                    "preferred_name": profile.preferred_name,
                    "first_seen": profile.first_seen,
                    "last_seen": profile.last_seen,
                    "message_count": profile.message_count,
                    "known_facts": profile.known_facts,
                    "relations": profile.relations,
                    "topics": profile.topics,
                    "notes": profile.notes,
                    "speaking_style": profile.speaking_style,
                    "catchphrases": profile.catchphrases,
                    "aliases": profile.aliases,
                }
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug("已保存 %d 个用户档案", len(data))
        except Exception:
            logger.exception("保存用户记忆失败")

    # ----------------------------------------------------------------
    # 用户操作
    # ----------------------------------------------------------------
    def get_or_create(self, wxid: str, display_name: str = "") -> UserProfile:
        """获取或创建用户档案"""
        if wxid not in self._users:
            self._users[wxid] = UserProfile(wxid=wxid)
            logger.info("新用户: %s (%s)", wxid, display_name or "未知")
        profile = self._users[wxid]
        if display_name:
            profile.update_name(display_name)
        return profile

    def get(self, wxid: str) -> Optional[UserProfile]:
        """获取用户档案，不存在返回 None"""
        return self._users.get(wxid)

    def record_message(self, wxid: str, display_name: str = ""):
        """记录用户的一次发言（自动创建 + 更新活动时间）"""
        profile = self.get_or_create(wxid, display_name)
        profile.record_activity()
        # 自动保存（每 10 条消息保存一次，避免频繁 IO）
        if profile.message_count % 10 == 0:
            self.save()

    def update_fact(self, wxid: str, key: str, value: str):
        """更新用户的一个已知事实"""
        profile = self.get_or_create(wxid)
        profile.set_fact(key, value)
        self.save()
        logger.info("用户 %s 事实更新: %s = %s", profile.preferred_name or wxid, key, value)

    def merge_fact(self, wxid: str, key: str, value: str) -> bool:
        """
        合并事实：如果已存在相似 key，更新而非覆盖。
        返回 True 表示有变更。
        """
        profile = self.get_or_create(wxid)
        # 检查是否已存在完全相同的事实
        if key in profile.known_facts and profile.known_facts[key] == value:
            return False
        # 检查是否有相似 key（如 "喜欢" vs "爱好"）
        similar_keys = {
            "喜欢": ["爱好", "偏好"],
            "爱好": ["喜欢", "偏好"],
            "职业": ["工作", "岗位"],
            "工作": ["职业", "岗位"],
        }
        for existing_key, existing_value in profile.known_facts.items():
            if existing_value == value:
                return False  # 值相同，key 不同也无所谓
            # 相似 key 合并
            related = similar_keys.get(key, [])
            if existing_key in related:
                profile.known_facts[key] = value
                del profile.known_facts[existing_key]
                self.save()
                return True

        profile.set_fact(key, value)
        self.save()
        return True

    def add_relation(self, wxid: str, target_wxid: str, relation: str):
        """记录两个用户之间的关系。"""
        profile = self.get_or_create(wxid)
        profile.relations[target_wxid] = relation
        self.save()

    def add_topic(self, wxid: str, topic: str):
        """记录用户讨论过的话题"""
        profile = self.get_or_create(wxid)
        if topic not in profile.topics:
            profile.topics.append(topic)
            if len(profile.topics) > 20:
                profile.topics = profile.topics[-20:]

    def update_notes(self, wxid: str, notes: str):
        """更新用户备注"""
        profile = self.get_or_create(wxid)
        profile.notes = notes
        self.save()

    def set_speaking_style(self, wxid: str, style: str, catchphrases: list = None):
        """更新用户的说话风格。"""
        profile = self.get_or_create(wxid)
        if style.strip():
            profile.speaking_style = style.strip()
        if catchphrases:
            profile.catchphrases = list(set(profile.catchphrases + catchphrases))[:10]
        self.save()

    def find_by_name(self, name: str) -> Optional[UserProfile]:
        """按 mention_name / 外号 / 历史名 / wxid 查找用户。mention_name 优先匹配。"""
        name_lower = name.lower().strip()
        # 先精确匹配 wxid
        if name_lower in self._users:
            return self._users[name_lower]
        for profile in self._users.values():
            # 匹配基准 @mention 名（最高优先级）
            if profile.mention_name and name_lower in profile.mention_name.lower():
                return profile
            # 匹配外号
            for alias in profile.aliases:
                if name_lower in alias.lower():
                    return profile
            # 匹配当前名字
            if name_lower in profile.preferred_name.lower():
                return profile
            # 匹配历史显示名
            for dn in profile.display_names:
                if name_lower in dn.lower():
                    return profile
            # 匹配 wxid
            if name_lower in profile.wxid.lower():
                return profile
        return None

    def add_alias(self, wxid: str, alias: str):
        """添加一个外号/别名"""
        profile = self.get_or_create(wxid)
        alias_clean = alias.strip()
        if alias_clean and alias_clean not in profile.aliases:
            profile.aliases.append(alias_clean)
            if len(profile.aliases) > 10:
                profile.aliases = profile.aliases[-10:]
            self.save()
            logger.info("用户 %s 新增外号: %s", profile.preferred_name or wxid, alias_clean)

    # ----------------------------------------------------------------
    # 群上下文
    # ----------------------------------------------------------------
    def get_users_context(self, wxids: List[str]) -> str:
        """
        为一组用户生成 LLM 上下文摘要。
        只返回有值得关注的信息的用户。
        """
        lines = []
        for wxid in wxids:
            profile = self.get(wxid)
            if not profile:
                continue
            summary = profile.get_context_summary()
            if summary:
                lines.append(f"  [{profile.preferred_name or wxid}]: {summary}")
        if lines:
            return "群成员信息:\n" + "\n".join(lines)
        return ""

    def get_user_context(self, wxid: str) -> str:
        """为单个用户生成 LLM 上下文（优先用显示名，不暴露 wxid）"""
        profile = self.get(wxid)
        if not profile:
            return ""
        name = profile.preferred_name or wxid
        summary = profile.get_context_summary()
        if summary:
            return f"[{name}]: {summary}"
        return f"[{name}]: 暂无已知信息"

    def get_all_wxids(self) -> list:
        """返回所有已知用户的 wxid 列表（供实体解析等使用）。"""
        return list(self._users.keys())

    @property
    def user_count(self) -> int:
        return len(self._users)
