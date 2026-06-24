# Bot Refactor PRD

**日期**: 2026-06-24  
**范围**: 中等重构 — 保留 WeFlow + UIA + DeepSeek API，重写数据层 + 核心管道  
**规格**: `docs/superpowers/specs/2026-06-24-bot-refactor-design.md`  
**计划**: `docs/superpowers/plans/2026-06-24-bot-refactor-plan.md`

## 目标

把当前补丁堆补丁的 bot 重构成干净的六阶段管道：Parse → Enrich → Prompt → LLM → Decode → Send。每个阶段纯函数，Store 是唯一共享状态。

## 成功标准

1. **认识人**: 名字解析准确率 100%（mention_name + aliases 查找，不暴露 wxid）
2. **记住事**: 事实带置信度，高低置信度有冲突保护
3. **说人话**: 一条回复一段说完，不拆段，不发 tool call 原文
4. **懂上下文**: 非@消息进历史，启动加载 20 条，群聊摘要定期更新
5. **不掉链子**: API 失败有 fallback，发送失败不崩溃，磁盘写入原子化

## 非目标

- 不换 WeFlow 通信层
- 不换 UIA 发送层
- 不换 DeepSeek API
- 不重新设计人格/system prompt（保持孙吧风格）
- 主动发言、Web 面板二期再做

## 技术决策

| 决策 | 选择 |
|------|------|
| 架构模型 | 六阶段管道（纯函数 + Store） |
| 数据存储 | 单一 `data/store.json`，原子写入 |
| 名字模型 | mention_name（基准）+ aliases（外号），弃用 preferred_name |
| 置信度 | FactEntry {value, source, confidence}，低不能覆高 |
| Prompt 格式 | 四段：系统指令 + 群聊摘要 + 最近10条 + 当前消息 |
| @mention | 内联发送（at_sender 开头，正文中@在出现位置） |

## Issue 拆分

共 17 个独立 task，按依赖关系分组为 6 个 issue：

### Issue 1: Store 数据层
**依赖**: 无  
**覆盖 Task**: 1-4  
**产出**: `src/store.py` — Person, Group, Memory, ChatMsg, Store CRUD, JSON save/load, 名字解析, 历史管理  
**可测试**: `tests/test_store.py` — 24 个测试  

### Issue 2: Parse + Enrich 阶段
**依赖**: Issue 1  
**覆盖 Task**: 5-6  
**产出**: `src/parse.py` — 消息解析；`src/enrich.py` — 上下文充实  
**可测试**: `tests/test_parse.py` + `tests/test_enrich.py`  

### Issue 3: Prompt + LLM 阶段
**依赖**: Issue 2  
**覆盖 Task**: 7-8  
**产出**: `src/prompt.py` — 四段式 prompt；`src/llm.py` — API 调用 + tool use + fallback  

### Issue 4: Decode + Send 阶段
**依赖**: Issue 3  
**覆盖 Task**: 9-10  
**产出**: `src/decode.py` — 回复解码；`src/send.py` — 内联 @mention 发送  

### Issue 5: Pipeline + Entry Point
**依赖**: Issue 4  
**覆盖 Task**: 11, 13, 15  
**产出**: `src/pipeline.py` — 主循环；`src/config.py` — 配置；`main.py` — 入口  

### Issue 6: WeFlow + UIA 适配 + 迁移
**依赖**: Issue 5  
**覆盖 Task**: 12, 14, 16, 17  
**产出**: `src/weflow.py`, `src/uia.py`, `src/migrate.py`, 旧文件清理  
