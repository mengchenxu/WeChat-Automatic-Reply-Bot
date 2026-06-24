# Issue 4: Decode + Send 回复处理

## What to build

bot 的"嘴手"——拿到 LLM 回复后，提取 @mentions、/remember 指令、纠正信号，然后通过 UIA 键盘模拟发到微信。

### 端到端行为

1. LLM 回复 → Decode 阶段提取内联 @mentions、/remember 指令、/context 更新、纠正信号
2. 清理回复文本（去格式垃圾、去@发送者）
3. Decode 结果 → Send 阶段：at_sender 在开头，正文中 @name 在出现位置实时转键盘@
4. 返回 store mutations（新增事实、更新摘要、纠正事实）

### 关键规则

- 内联 @mention：不把所有人 @堆在开头
- /remember @某人 key: value → source=manual, confidence=0.8
- 纠正信号 "我不是xxx" → correct_fact() 无视置信度
- 发送失败不崩溃

## Acceptance criteria

- [ ] @mention 内联提取正确
- [ ] /remember /context 指令正确解析
- [ ] "我不是小乐" → correct_fact 触发
- [ ] 发送失败日志记录，bot 继续运行

## Blocked by

- #3 Prompt + LLM 回复生成

## Parent

PRD: `docs/superpowers/specs/2026-06-24-bot-refactor-PRD-final.md`
