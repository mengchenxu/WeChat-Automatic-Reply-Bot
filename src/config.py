"""配置加载 — 从 config.yaml 读取并返回结构化配置"""
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
    max_tokens: int = 2048
    temperature: float = 0.85


@dataclass
class BotConfig:
    name: str = "鼠鼠"
    system_prompt: str = ""
    reply_cooldown_seconds: int = 3


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    weflow_token: str = ""
    enable_search: bool = True


def load_config(path: str = "config/config.yaml") -> AppConfig:
    """从 YAML 文件加载配置。"""
    if not os.path.exists(path):
        return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config = AppConfig()

    llm_raw = raw.get("llm", {})
    config.llm = LLMConfig(
        provider=llm_raw.get("provider", "deepseek"),
        api_key=llm_raw.get("api_key", ""),
        base_url=llm_raw.get("base_url", "https://api.deepseek.com"),
        model=llm_raw.get("model", "deepseek-chat"),
        max_tokens=llm_raw.get("max_tokens", 2048),
        temperature=llm_raw.get("temperature", 0.85),
    )

    bot_raw = raw.get("bot", {})
    config.bot = BotConfig(
        name=bot_raw.get("name", "鼠鼠"),
        system_prompt=bot_raw.get("system_prompt", ""),
        reply_cooldown_seconds=bot_raw.get("reply_cooldown_seconds", 3),
    )

    config.weflow_token = raw.get("weflow_token", "")
    config.enable_search = bot_raw.get("enable_search", True)

    return config
