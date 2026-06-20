# 群聊机器人记忆升级设计

**日期**：2026-06-20
**状态**：设计完成，待实施
**目标**：全面提升机器人记忆能力——短期对话连贯、长期跨会话记忆、主动关联回忆、理解网络热梗

---

## 一、问题分析

当前机器人 (`deepseek-chat` / WeFlow / UIA) 的主要短板：

1. **对话记忆有限**：只用 deque 保留最近 30 条消息，稍长的讨论就丢失上下文
2. **无法跨会话回忆**：除了手动 `/remember` 指令，没有自动提取和检索历史信息的能力
3. **回复机械**：system prompt 简单，不会主动关联过去的记忆
4. **不懂网络梗**：遇到流行语、热梗只能瞎猜或无视

## 二、架构改进：三层记忆体系

```
新消息 → WeFlowClient → BotCore → 梗检测(可选搜索) → 记忆检索 → 上下文组装 → LLM → 记忆提取 → 发送
                              │              │                │
                              ▼              ▼                ▼
                        GroupMemoryStore  UserMemoryStore  自动更新三层记忆
                        (情景记忆-新增)    (语义记忆-增强)   (工作/情景/语义)
```

### 2.1 工作记忆（Working Memory）

- **位置**：`src/bot_core.py` — `GroupSession` 数据结构增强
- **保持**：最近 20 条完整消息 + 当前话题摘要 + 话题关键词
- **更新频率**：每 10 条消息触发一次话题摘要更新（增量式）
- **生命周期**：内存中，重启丢失（有情景记忆兜底）

### 2.2 情景记忆（Episodic Memory）

- **位置**：新增 `src/group_memory.py` — `GroupMemoryStore`
- **持久化**：`data/group_memories.json`
- **内容**：群内值得记住的事件/决定/趣事，每条包含：内容、分类、参与用户、关键词、时间戳、重要度
- **提取方式**：每 ~10 条消息，LLM 自动扫描并提取新记忆
- **检索方式**：关键词匹配 + 时间衰减打分，top-3 注入上下文
- **过期清理**：超过 30 天的低重要度记忆自动清理

### 2.3 语义记忆（Semantic Memory）

- **位置**：`src/user_memory.py` — `UserProfile` 增强
- **已有功能保留**：名字追踪、已知事实、常聊话题、备注
- **新增功能**：
  - 自动事实提取（LLM 每轮自动判断，不再只靠 `/remember` 指令）
  - 关系追踪（`relations: dict` — 记录用户间关系如"同事""朋友"）
  - 事实去重合并（新旧事实相似时合并）

---

## 三、上下文组装（核心变革）

**改动文件**：`src/context_builder.py`

### 3.1 检索流程

1. 获取当前话题关键词（工作记忆维护的 `topic_keywords`）
2. 用关键词检索 `GroupMemoryStore`（本地关键词匹配，无 LLM 开销）
3. 检索发言人 + 被 @ 者的用户档案
4. 按优先级组装

### 3.2 上下文结构（注入到 LLM user message 中）

```
[群聊背景]          ← 长期群背景摘要
[相关记忆]          ← GroupMemoryStore 检索结果（最多3条，带时间衰减）
[当前话题]          ← 工作记忆中实时维护的话题摘要
[参与成员]          ← UserMemoryStore 档案
[热梗参考]          ← 如果有搜索结果则注入
[最近对话]          ← 最近 N 条消息
[消息内容]          ← 原始用户消息
```

### 3.3 主动关联

当检索到的记忆与当前消息高度相关时，在 prompt 中附加引导：

> 你可能还记得：[记忆内容]。如果合适，可在回复中自然提及。

---

## 四、热梗理解（Tool Use）

**改动文件**：`src/llm_client.py` + 新增 `src/web_search.py`

### 4.1 机制

给 LLM 添加 `search_web` 工具，由 LLM 自行判断是否需要搜索：

1. 第一轮调用：发送 messages + tools → LLM 决定是否调用 search_web
2. 如需搜索：执行 DuckDuckGo 搜索 → 结果作为 tool result 注入 → 第二轮调用生成最终回复
3. 最多 2 轮，防止无限循环

### 4.2 搜索工具定义

```python
{
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "搜索网络了解不懂的网络用语、梗、流行语、新闻事件。遇到不确定含义的内容时使用。",
        "parameters": {"query": "搜索关键词，如：'电子布洛芬 是什么梗'"}
    }
}
```

### 4.3 搜索后端

DuckDuckGo（免费，无需 API key，支持中文），`duckduckgo_search` 库。

### 4.4 接梗行为

系统 prompt 要求 LLM：

> 搜索结果只是参考，不要生硬引用"我查了一下这个梗是……"，用你自己的话自然地接住。

---

## 五、System Prompt 重写

新 prompt 按五层组织：人格 → 记忆 → 交互 → 工具 → 约束

```
[人格设定]
你是微信群里的 AI 助手"鼠鼠"。
- 性格：幽默、接梗快、偶尔毒舌但不伤人、有好奇心
- 发言风格：口语化、带表情符号、不客套、像真实群友
- 身份：群里最了解每个人的人，默默记住大家的喜好和故事

[记忆使用]
上下文中会附带 [相关记忆]、[群内成员]、[当前话题] 等信息。
- 如果 [相关记忆] 中有和当前话题相关的内容，自然地提及（如"上次你说过……"）
- 用 [群内成员] 中的信息来个性化回复，用他们喜欢的称呼
- 不强行引用——实在不相关就不提

[交互原则]
1. 被 @ 时才回复
2. 每次 1-3 句话，简洁有料
3. 宁可不回，不要敷衍
4. 优先用记忆让对话更亲切

[工具使用]
- 遇到不认识的网络用语/梗/流行语 → search_web 搜索 → 自然接住，不解释梗本身

[硬约束]
- 不提及自己是 AI
- 不确定的事说不知道
- 不编造信息
- 不涉及政治敏感内容
```

---

## 六、文件改动清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `config/config.yaml` | 修改 | system_prompt 重写、新增搜索开关配置 |
| `src/llm_client.py` | 重写 | tool use 循环、search_web 工具定义 |
| `src/web_search.py` | **新增** | DuckDuckGo 搜索封装 |
| `src/group_memory.py` | **新增** | 情景记忆存储/检索/清理/LLM 提取 |
| `src/bot_core.py` | 修改 | GroupSession 增强（topic_summary/topic_keywords） |
| `src/context_builder.py` | 重写 | 三层记忆检索 + 上下文组装 + 自动事实提取 + 梗参考注入 |
| `src/user_memory.py` | 修改 | relations 字段、自动事实去重合并 |
| `main.py` | 修改 | GroupMemoryStore 初始化注入 |
| `requirements.txt` | 修改 | 添加 `duckduckgo_search` |

---

## 七、实施顺序

1. `group_memory.py` — 情景记忆（无依赖，先跑通）
2. `web_search.py` + `llm_client.py` — 搜索 + tool use
3. `user_memory.py` — 增强语义记忆
4. `bot_core.py` — GroupSession 增强
5. `context_builder.py` — 重写上下文组装（依赖 1-4）
6. `config/config.yaml` — system prompt 更新
7. `main.py` + `requirements.txt` — 集成上线
