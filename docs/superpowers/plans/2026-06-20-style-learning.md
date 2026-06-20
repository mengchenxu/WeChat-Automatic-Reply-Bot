# 群聊风格学习系统 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 机器人监听所有群消息，学习群整体风格和核心成员的个人风格，持续自动更新，使回复越来越像真人。

**Architecture:** 新增 StyleObserver 负责消息缓冲和实时统计；LLMClient 新增 analyze_style() 周期性地从缓冲区提取风格特征；GroupSession 和 UserProfile 各新增风格字段持久化存储；context_builder 注入风格段落；system_prompt 追加风格适应指导。

**Tech Stack:** Python 3.x, deepseek-chat, dataclasses + JSON 持久化, collections.Counter

## Global Constraints

- 新增文件放入 `src/`，数据持久化到现有 JSON 文件
- 所有 LLM 调用通过 `LLMClient` 统一管理
- 非 @ 消息仅用于学习观察，不触发回复
- 风格分析为轻量 LLM 调用（低 token，低温度）
- 回复仍通过 UIA 模拟键盘发送，不做改动

## File Map

| 文件 | 职责 | 改动 |
|------|------|------|
| `src/style_observer.py` | 消息缓冲 + 实时统计 + 触发判断 | 新建 |
| `src/bot_core.py` | GroupSession 新增风格字段；handle() 调用 observe | 修改 |
| `src/user_memory.py` | UserProfile 新增 speaking_style / catchphrases | 修改 |
| `src/llm_client.py` | 新增 analyze_style() 方法 | 修改 |
| `src/context_builder.py` | 上下文注入群风格段落 | 修改 |
| `config/config.yaml` | system prompt 补充风格适应 | 修改 |
| `main.py` | 集成 StyleObserver + 风格分析触发 + 非@消息处理 | 修改 |
| `src/weflow_client.py` | 移除 @bot 过滤，所有群消息通过 | 修改 |

---

### Task 1: StyleObserver 消息观察器

**Files:**
- Create: `src/style_observer.py`

**Interfaces:**
- Produces: `StyleObserver` class
  - `StyleObserver(max_buffer: int = 30)`
  - `.observe(room_id, wxid, display_name, content) -> None` — 记录一条消息
  - `.should_analyze(room_id) -> bool` — 缓冲区满 30 条返回 True
  - `.get_buffer(room_id) -> list[dict]` — 返回缓冲区消息列表，取完后清空
  - `.get_stats(room_id) -> dict` — 返回实时统计快照 `{top_words, top_emojis, avg_len}`
  - `.reset_buffer(room_id)` — 清空缓冲区

- [ ] **Step 1: 创建 `src/style_observer.py`**

```python
"""
风格观察器 — 监听所有群消息，维护缓冲区 + 实时统计，供定期风格分析使用。
"""
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RoomStats:
    """单个群的实时统计"""
    word_counter: Counter = field(default_factory=Counter)
    emoji_counter: Counter = field(default_factory=Counter)
    total_length: int = 0
    message_count: int = 0


class StyleObserver:
    """
    监听所有群消息，维护每个群的：
    - 消息缓冲区（供 LLM 定期分析风格）
    - 实时统计（词频、表情频率、平均句长）
    """

    def __init__(self, max_buffer: int = 30):
        self.max_buffer = max_buffer
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._stats: dict[str, RoomStats] = defaultdict(RoomStats)

    def observe(self, room_id: str, wxid: str, display_name: str, content: str):
        """记录一条群消息到缓冲区和统计。"""
        if not content.strip():
            return

        # 统计
        stats = self._stats[room_id]
        stats.message_count += 1
        stats.total_length += len(content)

        # 简单分词（按非中文字符/空格分割，并提取单个汉字序列）
        words = re.findall(r'[一-鿿]{2,}', content)
        for w in words:
            stats.word_counter[w.lower()] += 1

        # 提取表情
        emojis = re.findall(r'[\U0001F300-\U0001FAFF☀-➿✀-➿︀-️‍]', content)
        for e in emojis:
            stats.emoji_counter[e] += 1

        # 缓冲区
        self._buffers[room_id].append({
            "wxid": wxid,
            "name": display_name,
            "content": content,
        })

    def should_analyze(self, room_id: str) -> bool:
        """缓冲区满时返回 True。"""
        return len(self._buffers.get(room_id, [])) >= self.max_buffer

    def get_buffer(self, room_id: str) -> list[dict]:
        """返回缓冲区消息列表，取完后清空。"""
        msgs = list(self._buffers.get(room_id, []))
        self._buffers[room_id] = []
        return msgs

    def get_stats(self, room_id: str) -> dict:
        """返回当前统计快照。"""
        stats = self._stats.get(room_id)
        if not stats or stats.message_count == 0:
            return {"top_words": [], "top_emojis": [], "avg_len": 0}

        return {
            "top_words": [w for w, _ in stats.word_counter.most_common(15)],
            "top_emojis": [e for e, _ in stats.emoji_counter.most_common(5)],
            "avg_len": round(stats.total_length / stats.message_count, 1),
        }

    def reset_buffer(self, room_id: str):
        self._buffers[room_id] = []

    def reset_all(self, room_id: str):
        """重置某个群的缓冲区和统计。"""
        self._buffers.pop(room_id, None)
        self._stats.pop(room_id, None)
```

- [ ] **Step 2: 验证导入**

```bash
cd D:/chatbot && python -c "from src.style_observer import StyleObserver; o = StyleObserver(); o.observe('g1', 'u1', 'n1', '今天天气真好哈哈哈'); print('should:', o.should_analyze('g1')); print('stats:', o.get_stats('g1'))"
```

预期: `should: False`, stats 中有 `avg_len`

- [ ] **Step 3: Commit**

```bash
git add src/style_observer.py
git commit -m "feat: add style observer for message buffering and stats"
```

---

### Task 2: GroupSession 风格字段 + observe 调用

**Files:**
- Modify: `src/bot_core.py`

**Interfaces:**
- Consumes: `StyleObserver.observe(room_id, wxid, display_name, content)` from Task 1
- Produces: `GroupSession` 新增 `group_style: str`, `top_words: list`, `top_emojis: list`

- [ ] **Step 1: 修改 GroupSession dataclass**

在 `src/bot_core.py` 的 `GroupSession` 中，`message_since_memory` 后面追加：

```python
    # 风格学习字段
    group_style: str = ""               # LLM 生成的群风格描述
    top_words: list = field(default_factory=list)   # 高频词
    top_emojis: list = field(default_factory=list)  # 常用表情
```

- [ ] **Step 2: 在 BotCore.__init__ 中注入 StyleObserver**

```python
def __init__(self, config, weflow_client, user_memory=None, 
             style_observer=None, data_dir="data"):
    ...
    self.style_observer = style_observer  # StyleObserver，由 main.py 注入
```

- [ ] **Step 3: 在 handle() 中调用 observe**

在 `handle()` 方法中，`session.active_users.add(speaker_wxid)` 之后添加——**对所有消息都观察，不仅仅是 @bot 的**：

```python
# ---- 4.5 风格观察（所有群消息，含非@） ----
if self.style_observer:
    self.style_observer.observe(roomid, speaker_wxid, speaker_display, content)
```

- [ ] **Step 4: 添加风格更新的触发方法**

```python
def should_analyze_style(self, roomid: str) -> bool:
    """是否应该触发风格分析。"""
    if not self.style_observer:
        return False
    return self.style_observer.should_analyze(roomid)

def update_group_style(self, roomid: str, style_text: str, 
                       top_words: list, top_emojis: list):
    """更新群风格信息。"""
    session = self._get_session(roomid)
    if style_text.strip():
        session.group_style = style_text.strip()
    if top_words:
        session.top_words = top_words
    if top_emojis:
        session.top_emojis = top_emojis
```

- [ ] **Step 5: 验证**

```bash
cd D:/chatbot && python -c "from src.bot_core import GroupSession; s = GroupSession(group_id='test'); print('style:', repr(s.group_style), 'words:', s.top_words)"
```

- [ ] **Step 6: Commit**

```bash
git add src/bot_core.py
git commit -m "feat: add group style fields to GroupSession and observe integration"
```

---

### Task 3: UserProfile 风格字段

**Files:**
- Modify: `src/user_memory.py`

**Interfaces:**
- Produces: `UserProfile` 新增 `speaking_style: str`, `catchphrases: list`

- [ ] **Step 1: 修改 UserProfile dataclass**

在 `UserProfile` 的 `notes` 字段后追加：

```python
    # 风格学习字段
    speaking_style: str = ""            # LLM 生成的个人风格描述（1句话）
    catchphrases: list = field(default_factory=list)  # 口头禅
```

- [ ] **Step 2: 更新 load() 和 save()**

在 `load()` 中添加：
```python
speaking_style=d.get("speaking_style", ""),
catchphrases=d.get("catchphrases", []),
```

在 `save()` 中添加：
```python
"speaking_style": profile.speaking_style,
"catchphrases": profile.catchphrases,
```

- [ ] **Step 3: 在 UserMemoryStore 中添加 set_speaking_style 方法**

```python
def set_speaking_style(self, wxid: str, style: str, catchphrases: list = None):
    """更新用户的说话风格。"""
    profile = self.get_or_create(wxid)
    if style.strip():
        profile.speaking_style = style.strip()
    if catchphrases:
        profile.catchphrases = list(set(profile.catchphrases + catchphrases))[:10]
    self.save()
```

- [ ] **Step 4: 更新 get_context_summary 包含风格信息**

在 `get_context_summary` 中，`parts` 列表合适位置添加：
```python
    if self.speaking_style:
        parts.append(f"风格: {self.speaking_style}")
```

- [ ] **Step 5: 验证**

```bash
cd D:/chatbot && python -c "from src.user_memory import UserMemoryStore; u = UserMemoryStore(); u.set_speaking_style('test_u', '说话直接爱反问', ['不是', '你搁这']); p = u.get('test_u'); print(p.get_context_summary())"
```

- [ ] **Step 6: Commit**

```bash
git add src/user_memory.py
git commit -m "feat: add speaking style fields to UserProfile"
```

---

### Task 4: LLM 风格分析

**Files:**
- Modify: `src/llm_client.py`

**Interfaces:**
- Produces: `LLMClient.analyze_style(messages: list[dict]) -> dict`
  - 返回 `{"group_style": str, "top_words": [str], "top_emojis": [str], "user_styles": {wxid: {"style": str, "catchphrases": [str]}}}`

- [ ] **Step 1: 在 LLMClient 中添加 analyze_style 方法**

```python
def analyze_style(self, messages: list[dict]) -> dict:
    """
    分析一组群聊消息，提取群风格和关键发言人的个人风格。
    
    messages: [{"wxid": "...", "name": "...", "content": "..."}, ...]
    返回: {"group_style": str, "top_words": [str], "top_emojis": [str], 
           "user_styles": {wxid: {"style": str, "catchphrases": [str]}}}
    """
    if not messages:
        return {"group_style": "", "top_words": [], "top_emojis": [], "user_styles": {}}

    # 构建分析 prompt
    lines = []
    for m in messages[-30:]:
        lines.append(f"[{m['name']}]: {m['content'][:150]}")
    
    prompt = f"""分析以下群聊消息的风格特征。

消息:
{chr(10).join(lines)}

请返回 JSON（不要其他文字）：
{{
  "group_style": "群的总体说话风格，1-2句话概括。例如：互怼但不伤和气、喜欢用反问句、正经话题撑不过三句",
  "top_words": ["高频词1", "高频词2", "高频词3"],
  "top_emojis": ["常用表情1", "常用表情2"],
  "user_styles": {{
    "发言者wxid": {{
      "style": "该成员的说话风格，1句话",
      "catchphrases": ["口头禅1", "口头禅2"]
    }}
  }}
}}

注意：
- user_styles 只包含发言 >= 5 条的活跃成员
- 口头禅必须是该成员在消息中实际使用过的短语
- 只返回 JSON，不要 markdown 代码块"""

    try:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一个语言风格分析师。只返回 JSON，不要其他文字。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.3,
        )
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        import json
        result = json.loads(text)
        logger.info("风格分析完成: 群=%s, 用户=%d",
                    result.get("group_style", "")[:40],
                    len(result.get("user_styles", {})))
        return result
    except Exception:
        logger.exception("风格分析失败")
        return {"group_style": "", "top_words": [], "top_emojis": [], "user_styles": {}}
```

- [ ] **Step 2: 验证**

```bash
cd D:/chatbot && python -c "from src.llm_client import LLMClient; print('Import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/llm_client.py
git commit -m "feat: add style analysis to LLMClient"
```

---

### Task 5: 上下文注入群风格

**Files:**
- Modify: `src/context_builder.py`

- [ ] **Step 1: 修改 build_llm_context 函数签名和实现**

在 `build_llm_context` 的 sections 中，`[群聊背景]` 之后插入风格段落：

```python
def build_llm_context(
    msg: WeFlowMessage,
    session: GroupSession,
    user_memory: UserMemoryStore,
    group_memory: GroupMemoryStore,
    bot_nicknames: list,
    search_result: str = "",
) -> str:
    ...
    # ---- 1. 群聊背景 ----
    if session.group_context:
        parts.append(f"[群聊背景]\n{session.group_context}")

    # ---- 1.5 群内风格（新增） ----
    style_parts = []
    if session.group_style:
        style_parts.append(f"整体风格: {session.group_style}")
    if session.top_words:
        style_parts.append(f"高频词: {', '.join(session.top_words[:10])}")
    if session.top_emojis:
        style_parts.append(f"常用表情: {' '.join(session.top_emojis[:5])}")
    if style_parts:
        parts.append("[群内风格]\n" + "\n".join(style_parts))

    # ---- 1.6 核心成员风格（新增） ----
    if session.active_users:
        styled_users = []
        for wxid in session.active_users:
            profile = user_memory.get(wxid)
            if profile and profile.speaking_style:
                name = profile.preferred_name or wxid
                styled_users.append(f"  {name}: {profile.speaking_style}")
        if styled_users:
            parts.append("[核心成员风格]\n" + "\n".join(styled_users))

    # ---- 2. 相关情景记忆 ----
    ...
```

- [ ] **Step 2: 验证**

```bash
cd D:/chatbot && python -c "from src.context_builder import build_llm_context; print('Import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/context_builder.py
git commit -m "feat: inject group style and member style into LLM context"
```

---

### Task 6: System Prompt 补充

**Files:**
- Modify: `config/config.yaml`

- [ ] **Step 1: 在 system_prompt 中追加风格适应段落**

在 `[硬约束]` 之前插入：

```yaml
    [风格适应]
    上下文中会附带 [群内风格] 和 [核心成员风格]，这是群里真实的说话习惯。
    - 自然地融入这些风格特征：用他们常用的词、模仿群里的语气
    - 看到群里有特色的说话方式，可以学着用
    - 不要生硬复制或逐句模仿——你是群里的一员，不是复读机
    - 风格融入要润物细无声，让人感觉"鼠鼠越来越懂我们了"
```

- [ ] **Step 2: 验证配置加载**

```bash
cd D:/chatbot && python -c "from src.config_loader import load_config; c = load_config(); print('OK:', '风格适应' in c.bot.system_prompt)"
```

预期: `OK: True`

- [ ] **Step 3: Commit**

```bash
git add config/config.yaml
git commit -m "feat: add style adaptation section to system prompt"
```

---

### Task 7: Main 集成

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 修改 weflow_client._poll() — 移除 @bot 过滤**

当前 `_poll()` 中只允许 @bot 消息到达回调。风格学习需要所有消息都通过。将 `src/weflow_client.py` 中这行删除：

```python
            # 只响应 @鼠鼠 的消息
            if not self.is_at_bot(msg):
                continue
```

替换为注释说明：

```python
            # 所有群消息都交给回调（非 @ 用于风格学习，@ 用于回复）
```

- [ ] **Step 2: 修改 main.py on_msg — 增加非 @ 消息的 observe-only 路径**

在 `on_msg()` 中，`bot.handle(msg)` 之后增加一条路径：如果非 @bot，仍记录用户活动并观察风格，但直接 return：

```python
def on_msg(msg: WeFlowMessage):
    ...
    if not msg.is_group:
        return
    
    roomid = msg.roomid
    speaker_wxid = msg.sender_name

    # 所有消息都过 BotCore.handle（记录用户、观察风格）
    result = bot.handle(msg)
    
    # 非 @bot — 不做任何回复，直接返回
    if not client.is_at_bot(msg):
        return
    
    # 以下为 @bot 的正常回复流程
    if result is not None:
        ...
```

- [ ] **Step 3: 在 main.py 中初始化 StyleObserver**

```python
from src.style_observer import StyleObserver

def main():
    ...
    # 风格观察器
    style_observer = StyleObserver(max_buffer=30)
    logger.info("Style observer initialized (buffer=30)")
    
    ...
    # 注入到 BotCore
    bot = BotCore(config, client, user_memory=user_memory,
                  style_observer=style_observer, data_dir="data")
    
    ...
    def on_msg(msg: WeFlowMessage):
        ...
        # 在 LLM 回复之后，添加风格分析触发
        ...
        
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
```

- [ ] **Step 2: 验证启动**

```bash
cd D:/chatbot && python -c "from main import main; print('Import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: integrate style observer and analysis into main loop"
```

---

## 验证方案

1. **启动测试**：
```bash
cd D:/chatbot && python main.py
```
预期日志：
- Style observer initialized
- Bot started (WeFlow + DeepSeek + 3-tier Memory + Search + Style Learning)

2. **风格学习测试**：在群里正常聊天（不@），攒够 30 条后：
   - 日志出现 "触发风格分析"
   - 日志出现 "群风格已更新" / "用户风格已更新"
   - 之后 @鼠鼠 时，回复中包含群风格特征

3. **统计验证**：检查 `data/users.json` 中是否有 `speaking_style` 和 `catchphrases` 字段
