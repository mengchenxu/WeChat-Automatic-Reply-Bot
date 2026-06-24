# Issue 5: Pipeline 编排 + 入口

## What to build

把 Parse→Enrich→Prompt→LLM→Decode→Send 串成主循环，加上配置加载和启动流程。

### 端到端行为

1. 启动 → 加载 config → 加载 Store → 同步联系人 → 加载最近 20 条消息到历史 → 启动 WeFlow 轮询
2. 轮询收到消息→Parse→Enrich→（非@:更新历史返回）（@bot:Prompt→LLM→Decode→Send→更新 Store）
3. 每 15 条消息触发群聊摘要更新（复用 LLM 调用）
4. 定时（30min）刷新 name_cache + 同步新群成员
5. Ctrl+C 退出 → save Store

### 关键规则

- 管道各阶段是纯函数，pipeline.py 只做编排
- main.py ≤ 50 行，只管启动
- config.py 加载 YAML 配置（模型、system prompt、冷却时间等）

## Acceptance criteria

- [ ] bot 启动 → 轮询 → 收消息 → 回复 → 完整链路跑通
- [ ] 非 @消息进历史，@消息正常回复
- [ ] 启动加载 20 条历史消息
- [ ] 群聊摘要定期更新
- [ ] Ctrl+C 正常退出并保存

## Blocked by

- #4 Decode + Send 回复处理  

## Parent

PRD: `docs/superpowers/specs/2026-06-24-bot-refactor-PRD-final.md`
