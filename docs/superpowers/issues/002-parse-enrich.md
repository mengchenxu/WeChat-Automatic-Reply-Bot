# Issue 2: Parse + Enrich 消息理解

## What to build

bot 的"眼耳"——收到微信消息后，解析出谁发的、@了谁、提到了谁、是不是命令，然后把名字映射到真实的人。

### 端到端行为

1. 收到 WeFlow 原始消息 → Parse 阶段提取 sender、@mentions、命令、清理文本
2. Parse 结果进入 Enrich 阶段 → 解析名字（查 Store）、检索相关群记忆、扫描正文中的已知别名
3. 非 @bot 消息：更新 Store 聊天历史后直接返回，不走 LLM
4. @bot 消息：返回 `EnrichedCtx` 供 Prompt 阶段使用

### 关键规则

- 清理 @bot_name 时保留其他人 @mention（`@鼠鼠 @子南 你好` → `@子南 你好`）
- 去掉 WeChat 分隔符 U+2005
- 名字解析绝不返回 wxid，返回 mention_name
- 扫描正文中已知 aliases（"南哥在干嘛" → 找到 mention_name="子南"）
- 不把自己列入"在场的人"

## Acceptance criteria

- [ ] Parse 6 个测试通过（@bot、@多人、命令、分隔符、非@）
- [ ] Enrich 3 个测试通过（名字解析、记忆检索、别名扫描）
- [ ] `@鼠鼠 @子南 你认识他吗` → LLM 看到 `@子南 你认识他吗`
- [ ] 非@消息 → 正确记录到 Store 历史，不触发 LLM

## Blocked by

- #1 Store 统一数据层

## Parent

PRD: `docs/superpowers/specs/2026-06-24-bot-refactor-PRD-final.md`
