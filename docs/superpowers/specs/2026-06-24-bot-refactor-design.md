# 群聊 AI 机器人重构设计规格

**日期**: 2026-06-24  
**范围**: 中等重构 — 保留 WeFlow + UIA + DeepSeek API，重写数据层 + 核心管道  
**目标**: 认识人、记住事、说人话、懂上下文、不掉链子

---

## 1. 统一数据层 (Store)

合并 `users.json` + `group_memories.json` + `group_contexts.json` 为单一 `data/store.json`。

### Schema

```json
{
  "meta": {"version": 1, "last_sync": 1234567890},
  "people": {
    "<wxid>": {
      "mention_name": "子南",
      "aliases": ["南子"],
      "facts": [
        {"key": "xp系统", "value": "喜欢人马片", "source": "llm_extract", "confidence": 0.6}
      ],
      "catchphrases": ["评价一下"],
      "first_seen": 1234567890,
      "last_seen": 1234567890
    }
  },
  "groups": {
    "<room_id>": {
      "name": "子南的后宫",
      "context": "群内在聊抽象兄弟团...",
      "topic": "子南vs咚咚番号大战",
      "memories": [
        {"id": "abc123", "text": "子南压力大就看人马片", "keywords": ["子南","人马"], "category": "joke", "importance": 3, "timestamp": 1234567890}
      ],
      "last_msg_at": 1234567890
    }
  }
}
```

### 关键决策

- **弃用 `preferred_name`** — 名字只有两个槽：`mention_name`（基准）和 `aliases`（外号）
- **Facts 是列表** — 有序，按置信度过滤，不需要"相似 key 合并"
- **mention_name 永不覆盖** — 由群成员 API 写入，定时刷新；变动时旧名自动入 aliases
- **原子写入** — 先写 temp 再 rename，断电不丢数据
- **单一入口** — `Store.load()` / `Store.save()` / `Store.apply(mutations)`

---

## 2. 管道架构

六个纯净阶段，Store 只在首尾读写。

```
Parse → Enrich → Prompt → LLM → Decode → Send
```

### 2.1 Parse
- 提取 sender, @mentions, 命令 (/help 等)
- 去掉 WeChat 分隔符 (U+2005)
- 只去 @bot_name，保留其他人 @

### 2.2 Enrich
- 名字解析（见 §3）
- 检索相关群记忆（关键词匹配 + 时间衰减）
- 组装上下文数据结构
- 非@bot 消息在此返回，不继续下游

### 2.3 Prompt
- 四段式输出（见 §4）

### 2.4 LLM
- 调 DeepSeek API，处理 tool_use (search_web)
- 超时/限流重试 + fallback 文案
- 模型参数可配置

### 2.5 Decode
- 提取 @mentions, /remember, /context
- 清理回复文本
- 纠正信号检测（"我不是xxx" → correct_fact）
- 返回 (clean_reply, store_mutations)

### 2.6 Send
- 内联 @mention（UIA 实时切换文字粘贴和键盘@）
- at_sender 开头，正文 @在出现位置

---

## 3. 名字解析

### 查找优先级

1. 精确匹配 `mention_name`
2. 精确匹配 `aliases` 中的某个
3. 子串匹配 `mention_name`（拉丁名用词边界）
4. 子串匹配 `aliases`
5. 全没找到 → 创建占位 Person (mention_name=输入, confidence=low)

### 关键规则

- 不搜 `preferred_name`（该字段不存在）
- 不搜 `display_names` 历史
- 不暴露 wxid 给 LLM
- 找不到时创建占位（bot 以后会学到真名）

### 实体扫描

- 只扫描：显式 @ 的人 + 消息正文中出现的已知 aliases
- 不扫描"所有已知用户"（避免 `一下` 匹配 `等一下`）
- 不把自己列进结果

---

## 4. Prompt 格式

四段式，LLM 看到的输入长这样：

```
[系统指令]
你是微信群里的"鼠鼠"，孙笑川吧14级黄牌。说话带😅💧，阴阳怪气但不当真。
回复规则：一条完整消息，不拆段。提到群成员用@名字（系统自动转为真实@mention）。
不编造事实。遇到不懂的梗用 search_web 查。

[群聊摘要]
之前群内在聊抽象兄弟团，子南和咚咚互相甩锅人马片的事。

[群聊记录]
贯一: @鼠鼠 你觉得niko强还是donk强
鼠鼠: niko关键局软脚虾，donk才是真大腿😅
子南: 别吵了来看我新找的番号
咚咚: @子南 你又开始了是吧
...（最多10条）

[当前消息]
@贯一: 猎鹰拿major？先让niko把关键局枪法练好再说吧😅
```

### 关键决策

- 系统指令大幅缩短（当前 ~2000 字 → ~150 字）
- 群聊记录只显示最近 10 条（不是 16）
- 不加 [在场的人] 结构化段（LLM 从聊天记录推断）
- 群聊摘要 1-2 句，LLM 定期更新
- 格式指令只留两行

---

## 5. Store 变更规则 (Decode 后果)

### /remember 指令
- source=manual, confidence=0.8
- 外号/别名 → 加入 aliases

### 事实写入
- `merge_fact()`: source=llm_extract, confidence=0.6
- 低置信度不能覆盖高置信度
- `correct_fact()`: 纠正信号触发，无视置信度

### 纠正信号
- 匹配模式: "我不是xxx", "你记错了", "谁告诉你xxx"
- 触发 `correct_fact()` 强制修正

### 记忆提取
- 每 15 条消息触发一次 LLM 提取（频率可在 config 调整）
- 返回 memories + person_updates
- mention_name 变更时旧名降级为 alias

### 群聊摘要更新
- 每 15 条消息触发一次（与记忆提取共用一次 LLM 调用）
- 返回 1-2 句更新后的摘要

---

## 6. 容错与兜底

### Send 阶段
- 发送失败不崩溃
- fallback 文案不暴露技术细节
- 不发 tool call 原始文本

### LLM 阶段
- 超时 → "思考超时了，稍等"
- 限流 → "问太快了，喘口气"
- 401/403 → "API 密钥配置有误"

### Store 阶段
- 加载失败 → 从空开始
- 保存失败 → 日志记录，不影响运行
- 原子写入 → 不断电丢数据

---

## 7. 文件结构

```
src/
├── store.py       — 统一数据层
├── pipeline.py    — 管道编排（主循环）
├── parse.py       — 消息解析
├── enrich.py      — 上下文充实（名字解析 + 记忆检索）
├── prompt.py      — LLM prompt 组装
├── llm.py         — LLM 调用 + tool use + fallback
├── decode.py      — 回复解码（@提取, /remember, /context, 纠正信号）
├── send.py        — UIA 内联 @mention 发送
├── config.py      — 配置加载
├── weflow.py      — WeFlow REST 客户端
├── uia.py         — UIA 键盘模拟
├── proactive.py   — 主动发言（二期）
└── web.py         — Web 面板（二期）
```

从 16 个文件缩减到 13 个，每个不超过 200 行。

---

## 8. 数据迁移

首次启动自动迁移：
1. 读取 `users.json` → 转为 Store people（mention_name 取 preferred_name 中非 wxid 的值）
2. 读取 `group_memories.json` → 转为 Store groups[room].memories
3. 读取 `group_contexts.json` → 转为 Store groups[room].context
4. 写入 `data/store.json`
5. 保留旧文件（加 .bak 后缀）
