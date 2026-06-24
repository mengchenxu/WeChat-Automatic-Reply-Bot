# Issue 1: Store 统一数据层

## What to build

bot 的"记忆本"——单一 JSON 文件 `data/store.json`，替代当前的三份分散文件。

### 端到端行为

1. bot 启动时从 `store.json` 加载所有数据，文件不存在则从空开始
2. bot 运行中通过 `Store` 对象读写数据（增删改查人、群记忆、聊天历史）
3. bot 关闭时原子写入 `store.json`（先写 temp 再 rename，断电不丢数据）

### 数据模型

```
Person — 每个人
  ├── mention_name — 基准 @名字（群昵称，永不污染）
  ├── aliases — 所有已知外号
  ├── facts — [{key, value, source, confidence}] 带置信度的认知
  └── catchphrases — 口头禅

Group — 每个群
  ├── context — 群聊摘要
  ├── topic — 当前话题
  ├── memories — [{id, text, keywords, category, importance, timestamp}]
  └── history — [ChatMsg] 最近聊天记录

ChatMsg — 单条消息 {role, content, sender_name, sender_wxid, timestamp}
```

### 关键规则

- mention_name 是单一真相来源，绝不替换为 wxid
- 事实写入有置信度保护：低置信度不能覆盖高置信度
- 名字解析优先级：mention_name 精确 > aliases 精确 > 子串匹配
- 找不到人时创建占位 Person（以后会学到真名）

## Acceptance criteria

- [ ] Store CRUD 全部 24 个测试通过
- [ ] JSON 原子写入：模拟断电场景不丢数据
- [ ] 名字解析：子南、贯一、咚咚 全部能找到正确的人
- [ ] 事实置信度：低置信度写入被高置信度拒绝

## Blocked by

None — 可以立即开始

## Parent

PRD: `docs/superpowers/specs/2026-06-24-bot-refactor-PRD-final.md`  
规格: `docs/superpowers/specs/2026-06-24-bot-refactor-design.md`  
计划: `docs/superpowers/plans/2026-06-24-bot-refactor-plan.md` (Task 1-4)
