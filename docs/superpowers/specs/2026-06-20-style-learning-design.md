# 群聊风格学习系统

**日期**：2026-06-20
**目标**：机器人监听所有群消息，从真人发言中学习群整体风格和核心成员的个人风格，持续自动更新，使回复越来越像真人。

---

## 一、核心概念

在现有三层记忆（工作/情景/语义）基础上，增加**第四层：风格记忆**。

| 层级 | 学什么 | 存储位置 |
|------|--------|----------|
| 群风格 (GroupStyle) | 高频词、表情偏好、句式、语气、梗列表 | GroupSession 新增字段 |
| 个人风格 (UserStyle) | 个人说话习惯、口头禅、语气词 | UserProfile 新增字段 |

## 二、数据流

```
所有群消息 → BotCore.handle() 
  │
  ├─ @bot → 正常 LLM 回复（注入群风格上下文）
  │
  └─ 所有消息（含非@）→ StyleObserver
       │
       ├─ 实时统计：词频 +1、表情计数 +1、句长统计
       │
       └─ 缓冲区满 30 条 → LLM 提取风格 → 更新 GroupStyle / UserStyle
```

## 三、组件设计

### 3.1 StyleObserver（新增文件 `src/style_observer.py`）

- 维护每个群的「消息缓冲区」（最多 30 条）
- 实时统计：词频、表情频率、平均句长
- 提供 `observe(msg)` 接口，bot_core 在 handle 中调用
- 提供 `should_analyze(roomid)` — 缓冲区满 30 条返回 True
- 提供 `get_stats(roomid)` — 返回当前统计快照

### 3.2 GroupStyle（bot_core.py GroupSession 新增）

```python
@dataclass
class GroupSession:
    ...
    # 新增风格字段
    group_style: str = ""           # LLM 生成的群风格描述
    group_top_words: list = ...     # 高频词 top-10
    group_top_emojis: list = ...    # 常用表情 top-5
```

### 3.3 UserStyle（user_memory.py UserProfile 新增）

```python
@dataclass
class UserProfile:
    ...
    # 新增风格字段
    speaking_style: str = ""        # LLM 生成的个人风格描述（1句话）
    catchphrases: list = ...        # 口头禅列表
```

### 3.4 风格分析（LLMClient 新增方法）

`llm_client.analyze_style(messages: list) -> dict`:
- 接收一组消息，返回风格分析结果
- 轻量 prompt，低温度，~300 tokens 输出
- 返回：群风格文本 + 各发言人的个人风格

## 四、上下文注入

在 `context_builder.py` 的上下文组装中，新增一个段落：

```
[群内风格]
<group_style 内容>

[核心成员风格]
<有风格记录的用户列表>
```

放在 `[群聊背景]` 之后、`[相关记忆]` 之前。

## 五、System Prompt 补充

在 system prompt 中增加：

```
[风格适应]
上下文中会附带 [群内风格] 和 [核心成员风格]，这是群里的真实说话习惯。
请自然地融入这些风格特征：用他们常用的词、模仿群里的语气。
但不要生硬复制——你是群里的一员，不是复读机。
```

## 六、文件改动

| 文件 | 改动 |
|------|------|
| `src/style_observer.py` | **新增** — 消息缓冲 + 实时统计 + 触发判断 |
| `src/bot_core.py` | GroupSession 新增 group_style/top_words/top_emojis；handle() 中调用 observe |
| `src/user_memory.py` | UserProfile 新增 speaking_style/catchphrases |
| `src/llm_client.py` | 新增 analyze_style() 方法 |
| `src/context_builder.py` | 上下文注入群风格 |
| `config/config.yaml` | system prompt 补充风格适应段落 |
| `main.py` | 集成 StyleObserver + 风格分析触发 |
