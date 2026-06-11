"""
配置加载 — 从 config/config.yaml 读取所有配置。
"""
import os
from dataclasses import dataclass, field
from typing import List

import yaml


@dataclass
class LLMConfig:
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    max_tokens: int = 1024
    temperature: float = 0.7


@dataclass
class BotConfig:
    name: str = "小助手"
    system_prompt: str = "你是一个有帮助的 AI 助手。"
    reply_cooldown_seconds: int = 3


@dataclass
class GroupConfig:
    whitelist: List[str] = field(default_factory=list)
    blacklist: List[str] = field(default_factory=list)


@dataclass
class SessionConfig:
    max_history_rounds: int = 10


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    groups: GroupConfig = field(default_factory=GroupConfig)
    session: SessionConfig = field(default_factory=SessionConfig)


def load_config(path: str = "config/config.yaml") -> AppConfig:
    """从 YAML 文件加载配置。文件不存在时返回默认值。"""
    if not os.path.exists(path):
        return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    config = AppConfig()
    if "llm" in data:
        config.llm = LLMConfig(**data["llm"])
    if "bot" in data:
        config.bot = BotConfig(**data["bot"])
    if "groups" in data:
        config.groups = GroupConfig(**data["groups"])
    if "session" in data:
        config.session = SessionConfig(**data["session"])
    return config
