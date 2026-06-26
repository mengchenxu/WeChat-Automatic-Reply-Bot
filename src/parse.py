"""消息解析阶段 — 提取 sender, @mentions, 命令, 去分隔符"""
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ParsedMsg:
    room_id: str
    sender_wxid: str
    sender_name: str
    content: str            # 已清理：去@bot、去 ，保留其他@mention
    raw_mentions: List[str] = field(default_factory=list)
    is_at_bot: bool = False
    is_command: bool = False
    command: str = ""
    command_args: str = ""


def _extract_mentions(raw_content: str, content: str) -> List[str]:
    """从 WeChat rawContent 提取 WeChat 真实 @mention 名字。"""
    mentions: List[str] = []
    source = raw_content if ":\n" in raw_content else content
    if ":\n" in source:
        _, rest = source.split(":\n", 1)
        for part in rest.split(" "):
            part = part.strip()
            if part.startswith("@"):
                name = part[1:].strip()
                # 拉丁名提取（含空格，如 "B L U E"）
                latin = re.match(r'([a-zA-Z][a-zA-Z0-9 ]*)', name)
                if latin:
                    name = latin.group(1).strip()
                else:
                    # 中文名提取（2-4 字）
                    cjk = re.match(r'([一-鿿぀-ゟ가-힯]{2,4})', name)
                    if cjk:
                        name = cjk.group(1).strip()
                if name and name not in mentions:
                    mentions.append(name)
    return mentions


def parse(msg, bot_names: List[str]) -> Optional[ParsedMsg]:
    """将 WeFlowMessage 或原始 dict 解析为 ParsedMsg。私聊返回 None。"""
    # 兼容 WeFlowMessage 对象和原始 dict
    if hasattr(msg, 'session_id'):
        talker = msg.session_id
        content = (msg.content or "").strip()
        raw_content = msg.raw.get("rawContent", "") if hasattr(msg, 'raw') else ""
        sender = msg.sender_name or ""
    else:
        talker = msg.get("talker", "") or msg.get("session_id", "")
        content = (msg.get("content", "") or "").strip()
        raw_content = msg.get("rawContent", "") or ""
        sender = msg.get("senderUsername", "") or ""

    if "@chatroom" not in talker:
        return None

    # 显示名
    if ":\n" in raw_content:
        sender_name = raw_content.split(":\n")[0]
    else:
        sender_name = sender

    # 提取所有 @mention，排除 bot 自己
    all_mentions = _extract_mentions(raw_content, content)
    mentioned_others = [m for m in all_mentions if m not in bot_names]

    # 检测 @bot
    is_at_bot = any(f"@{n}" in content for n in bot_names)

    # 清理文本：去分隔符 + 去@bot
    text = content
    text = text.replace(" ", "")
    for name in bot_names:
        text = re.sub(rf'@{re.escape(name)}\s*', '', text).strip()

    # 过滤 bot 自己的消息（防止主动发言被回收后自我回复）
    if sender_name in bot_names or sender in bot_names:
        return None

    # 命令检测
    is_cmd = False
    cmd = cmd_args = ""
    if is_at_bot and text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""
        is_cmd = True

    return ParsedMsg(
        room_id=talker,
        sender_wxid=sender,
        sender_name=sender_name,
        content=text,
        raw_mentions=mentioned_others,
        is_at_bot=is_at_bot,
        is_command=is_cmd,
        command=cmd,
        command_args=cmd_args,
    )
