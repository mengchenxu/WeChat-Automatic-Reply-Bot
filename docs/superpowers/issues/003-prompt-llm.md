# Issue 3: Prompt + LLM 回复生成

## What to build

bot 的"大脑"——拿到上下文后组装 prompt，调 DeepSeek API 生成回复。

### 端到端行为

1. 收到 `EnrichedCtx` → Prompt 阶段组装四段式 prompt
2. Prompt 发给 DeepSeek API → 处理 tool_use (search_web) → 返回最终回复文本
3. API 失败时有 fallback（超时、限流、401/403 各不同文案）

### Prompt 格式（四段）

```
[系统指令]  ≤150 字孙吧人设 + 回复规则
[群聊摘要]  1-2 句，LLM 定期更新
[群聊记录]  最近 10 条消息
[当前消息]  正在处理的消息
```

### 关键规则

- 不注入结构化元数据段（[在场的人]、[核心成员风格] 等全砍掉）
- 工具搜索 (search_web) 最多一轮 tool call
- fallback 文案不暴露技术细节
- 模型参数从 config 读取

## Acceptance criteria

- [ ] Prompt 输出符合四段格式
- [ ] search_web tool_use 正常工作
- [ ] API 超时 → "思考超时了，稍等"
- [ ] API 限流 → "问太快了，喘口气"
- [ ] 配置切换模型（deepseek-chat / deepseek-reasoner）只需改 config

## Blocked by

- #2 Parse + Enrich 消息理解

## Parent

PRD: `docs/superpowers/specs/2026-06-24-bot-refactor-PRD-final.md`
