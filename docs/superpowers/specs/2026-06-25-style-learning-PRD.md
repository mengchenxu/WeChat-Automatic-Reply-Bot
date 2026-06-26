# PRD: 群聊风格学习

**日期**: 2026-06-25
**标签**: `ready-for-agent`

---

## Problem Statement

当前 bot（猫娘）虽然有自己的固定人格，但对群里的梗、高频词、常用表情、人际关系一无所知。回复中无法融入群特有的氛围，缺乏"自己群里的人"的亲切感。

## Solution

被动学习群聊风格——实时统计表情/词频，复用已有的 LLM 记忆提取学梗和关系，注入到 prompt 上下文。猫娘人设是**不变的底色**，学来的群文化是**调味料**。

## User Stories

1. 作为群成员，bot 会在回复中偶尔用群里的流行词和梗，感觉很懂这个群
2. 作为群成员，bot 提到群友时知道他们之间的关系（同事/朋友），称呼和话题更自然
3. 作为群成员，bot 知道最近大家喜欢聊什么，接话不突兀
4. 作为群成员，bot 不会因为学了群风格而丢掉猫娘本色——"喵~"永远在句尾
5. 作为群主，风格学习是静默的，不额外调用 LLM（复用已有的记忆提取 + 纯统计）
6. 作为开发者，所有风格数据存在 `store.json` 中，不产生新数据文件

## Implementation Decisions

1. **Seam 范围**: Store（加字段）+ Pipeline（实时统计触发）+ Enrich（注入风格数据）+ Prompt（加风格段）+ Config（系统指令补充）
2. **数据模型新增**:
   - `Group.top_emojis: list[str]` — 高频表情 top-5
   - `Group.top_words: list[str]` — 高频词 top-10
   - `Person.relations` — 已有字段，LLM 记忆提取时顺带学
   - `Person.speaking_style` — 已有字段，LLM 记忆提取时顺带学
3. **实时统计**（无 LLM 开销）:
   - 每条消息进入 Pipeline 时，统计 emoji 和词频
   - 维护 Group 级别的计数器
   - 每 N 条消息取 top-N 写入 `top_emojis` / `top_words`
4. **LLM 提取**（复用 Issue 9 已有的 `_check_extract`）:
   - 在 `build_extraction_prompt` 的 facts 部分加"人际关系"
   - 让 LLM 在提取记忆时同时提取群友之间的关系
5. **风格注入位置**:
   - 系统 prompt：加一句"上下文会附带群内风格，自然地用这些梗和表情卖萌喵~"
   - 上下文：在 `[群聊摘要]` 后加 `[群内风格]` 段（常用表情 + 高频词 + 成员风格摘要）
6. **猫娘底线不变**: 句尾"喵~"、自称"人家"、叫群友"主人" 始终在系统 prompt 中，不会被覆盖

## Testing Decisions

- **什么是好测试**: 测试数据写入 Store、风格信息出现在 EnrichedCtx 中
- **测试文件**: `test_store.py`（新字段读写）、`test_enrich.py`（风格注入到 people）、`test_prompt.py`（风格段在 user message 中）
- **不测试**: LLM 实际输出质量、emoji 正则覆盖完整度

## Out of Scope

- 不改猫娘人设
- 不新增 LLM 调用（复用已有的记忆提取和摘要更新）
- 不加新的数据文件
- 主动发言系统（二期 B）

## Further Notes

- `Group.top_emojis` 和 `top_words` 实时更新不需要 LLM，纯正则 + 计数
- Person.relations 的 LLM 提取只需在 `build_extraction_prompt` 的 prompt 中加一行示例
- `to-issues` 下一步拆成 2 个 issue
