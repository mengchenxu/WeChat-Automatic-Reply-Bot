# 主动发言系统

**日期**：2026-06-20
**目标**：机器人能主动在群里找话题、发起对话，不再只被动等待@。

---

## 一、三种触发机制

| 机制 | 触发条件 | 话题来源 |
|------|----------|----------|
| 🥶 冷场暖场 | 群超过 N 分钟没人说话 | 群记忆 + 用户偏好 |
| ⏰ 定时推送 | 当前时间匹配配置的时段 | 群记忆 + 问候/闲聊 |
| 🔥 热点分享 | 距上次检查超过 N 小时 | 联网搜索新闻/热梗 |

**启动行为**：机器人上线时立即检查群已经冷场多久。如果已超过冷场阈值，在启动后等待 1-2 分钟（等 WeFlow 连接稳定）再触达。

## 二、防刷屏

- **动态频率**：群里聊得火热 → 不插嘴；冷场越久 → 越可能发言
- **每日上限**：可配置，默认 10 条/天
- **最小间隔**：两次主动发言至少间隔 30 分钟
- **静音时段**：可配置（如凌晨 2-6 点），期间不发声

## 三、话题生成流程

```
触发条件满足
  │
  ├─ 1. 检索上下文
  │     GroupMemoryStore.search() → 相关记忆
  │     UserMemoryStore → 群成员的兴趣/偏好
  │
  ├─ 2. 如果是热点模式 → search_web(热点话题)
  │
  ├─ 3. LLM 生成话题文案
  │     Prompt: 你是xxx，群里冷场/到时间了/有热点，
  │              基于群记忆和成员偏好，抛一个话题
  │
  └─ 4. send_text → 群聊
```

LLM 生成话题时的要求：
- 自然不突兀，不要说"我来活跃下气氛"之类的话
- 用群的说话风格（已有的 GroupStyle）
- 1-2 句话，简洁有料
- 如果群有未聊完的话题，优先续那个

## 四、新增组件

### `src/proactive_speaker.py`

```
ProactiveSpeaker
  ├─ 属性
  │   ├─ cold_silence_minutes: int
  │   ├─ schedule_times: list[str]
  │   ├─ hot_topic_interval_hours: int
  │   ├─ max_per_day: int
  │   ├─ min_interval_minutes: int
  │   ├─ quiet_hours: list[(str, str)]
  │   └─ _sent_today: int, _last_sent_at: float
  │
  ├─ should_speak(room_id, last_msg_time) -> bool
  │   综合判断是否应该主动发言
  │
  ├─ get_topic_reason(room_id, last_msg_time) -> str | None
  │   返回触发原因: "cold_silence" | "scheduled" | "hot_topic" | None
  │
  ├─ generate_topic(room_id, reason, session, llm, group_memory, user_memory) -> str
  │   LLM 生成话题文案
  │
  └─ record_sent()
      更新计数和时间
```

### 后台线程

在 `main.py` 中启动一个后台线程，每分钟执行：

```
每分钟检查:
  for each active_group:
    if not 静音时段 and should_speak(group, last_msg_time):
      topic = generate_topic(...)
      client.send_text(topic, room_id)  # 不 @ 任何人
      record_sent()
```

### 启动时的特殊处理

```python
def on_startup():
    for each group:
        silence = now - group.last_message_time
        if silence > cold_silence_minutes:
            # 等 90 秒让 WeFlow 连接稳定
            schedule_in(90s, lambda: cold_start_speak(group))
```

## 五、配置

```yaml
proactive:
  enabled: true                        # 是否启用主动发言
  cold_silence_minutes: 30             # 冷场阈值（分钟）
  schedule_times:                      # 定时发言（24h制）
    - "08:30"
    - "12:30"
    - "18:00"
    - "22:00"
  hot_topic_interval_hours: 4          # 热点检查间隔（小时）
  max_per_day: 10                      # 每日上限
  min_interval_minutes: 30             # 最小间隔（分钟）
  quiet_hours: ["02:00", "06:00"]      # 静音时段（起-止）
```

## 六、System Prompt 补充

```
[主动发言]
有时你会根据情况主动在群里说话（没人@你也可能开口）：
- 冷场时抛个话题活跃气氛
- 到点了发个日常问候
- 看到热点分享一下

主动发言也要保持你的风格——你是群里的一员，别客气但也别烦人。
```

## 七、文件改动

| 文件 | 改动 |
|------|------|
| `src/proactive_speaker.py` | **新增** |
| `config/config.yaml` | 新增 proactive 配置段 + system prompt 补充 |
| `main.py` | 初始化 ProactiveSpeaker + 后台线程 |
