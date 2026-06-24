# PRD: 群聊 AI 机器人重构

**类型**: 重构  
**规格**: `docs/superpowers/specs/2026-06-24-bot-refactor-design.md`  
**标签**: `ready-for-agent`

---

## Problem Statement

当前 bot 代码是补丁堆补丁——16 个文件、数据分散在三份 JSON、名字解析不可靠、上下文组装混乱、LLM 常被带偏。用户不能信任 bot 的基础能力：认识人、记住事、说连贯话。

## Solution

重构成干净的六阶段管道（Parse→Enrich→Prompt→LLM→Decode→Send）。每个阶段纯函数，Store 是单一共享状态。名字用 mention_name（基准）+ aliases（外号）两层模型，事实带置信度。@mention 内联发送。启动加载历史消息，非@消息自动进上下文。

## User Stories

1. 作为群成员，我 @bot 并同时 @另一个群友时，bot 能理解我 @那个人是为了向他介绍这个人
2. 作为群成员，我在消息正文中自然提到群友的名字（不加@），bot 能识别我指的是谁
3. 作为群成员，我换了群昵称后，bot 仍能通过旧名字找到我
4. 作为群成员，bot 在回复中用正确的群昵称 @我，不会出现 wxid_xxx 这种机器人感
5. 作为群成员，bot 提到其他群友时，@mention 出现在句子中提到的位置，而不是全部堆在开头
6. 作为群主，bot 不会把我公开说的事实（"我喜欢猎鹰"）和 LLM 自己推测的事实（"原名小乐"）混为一谈
7. 作为群成员，当我说"你记错了，我不叫小乐"时，bot 能纠正自己的错误记忆
8. 作为群成员，bot 的回复是一条完整消息，不会拆成多段发
9. 作为群成员，bot 重启后仍然知道群里刚才在聊什么
10. 作为群成员，bot 不会把其他群的聊天混到当前群里
11. 作为群成员，bot 不会在微信里发原始 tool call 文本或 API 错误信息
12. 作为开发者，我可以单独替换 LLM 模型而不影响其他模块
13. 作为开发者，每个管道阶段可以独立测试

## Implementation Decisions

1. **架构**: 六阶段管道，每阶段纯函数。Store 在管道首尾读写，中间不碰磁盘
2. **数据存储**: 单一 `data/store.json`，原子写入（temp → rename）。三份旧 JSON 首次启动自动迁移
3. **名字模型**: mention_name（来自群成员 API 的群昵称，定时刷新，变更时旧名入 aliases）+ aliases（外号，只增不减）。弃用 preferred_name
4. **事实模型**: FactEntry {key, value, source, confidence}。source 分 user_stated(0.9)、manual(0.8)、correction(0.95)、llm_extract(0.6)、auto_extract(0.4)、legacy(0.5)。低置信度不能覆盖高置信度
5. **纠正信号**: 匹配 "我不是xxx"、"你记错了"、"谁告诉你xxx" → 触发 correct_fact() 无视置信度覆盖
6. **Prompt 格式**: 四段——系统指令(≤150字) + 群聊摘要(1-2句) + 最近10条对话 + 当前消息。不加结构化元数据段
7. **@mention 发送**: 内联模式，at_sender 在开头，正文中 @在出现位置实时转为键盘@选人
8. **非@消息**: 全部进历史，不触发 LLM 但更新上下文
9. **启动加载**: 从 WeFlow API 拉每个群最近 20 条消息，静默记录
10. **容错**: API 超时/限流有 fallback 文案，UIA 发送失败不崩溃，磁盘写入失败不影响运行

## Testing Decisions

- **什么是好测试**: 只测外部行为（函数输入→输出），不测内部实现。测试读起来像规格说明
- **测试 seam**: 六个管道阶段 + Store = 七个 seam。每个 seam 一个测试文件
- **测试风格**: 每个测试一个行为，先写测试（RED），再写最小实现（GREEN），提交
- **不测试**: UIA 键盘模拟（需要真实微信窗口）、WeFlow REST 轮询（需要真实 WeFlow 进程）

## Out of Scope

- 不换人设（保持孙吧"鼠鼠"人格）
- 不换 WeFlow 通信层
- 不换 UIA 键盘模拟层
- 不换 DeepSeek API 提供商
- 主动发言系统（二期）
- Web 控制面板（二期）
- 多群 style 学习（二期）

## Further Notes

- `to-issues` 下一步拆成 6 个独立 issue
- 每个 issue 一个 `/implement` session，互不依赖或明确串行
- 旧数据迁移是最后一个 issue，确保新 bot 先跑通再迁移
