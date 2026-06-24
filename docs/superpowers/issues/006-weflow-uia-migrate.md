# Issue 6: WeFlow + UIA 适配 + 旧数据迁移 + 清理

## What to build

最后一块——把已验证的 WeFlow 通信层和 UIA 发送层迁移到新结构，旧 JSON 数据转成新 store.json，删旧文件。

### 端到端行为

1. WeFlow REST 客户端精简（从 weflow_client.py 提取核心：轮询、contacts API、group-members API）
2. UIA 键盘模拟保持（从 uia_sender.py 迁移，代码不变）
3. 数据迁移脚本：users.json → Store people、group_memories.json → Store memories、group_contexts.json → Store context
4. 迁移后旧文件加 .bak 后缀，保留不删
5. 删除旧 src/*.py 文件，保留 main.py 和 watchdog.py

### 关键规则

- WeFlow 客户端只保留 REST 轮询 + 联系人同步，去除非必要功能
- 数据迁移是幂等的（重复运行不产生重复数据）
- 旧文件不删只改名（安全第一）

## Acceptance criteria

- [ ] 数据迁移脚本正确运行（现有 users.json 数据全进 store.json）
- [ ] WeFlow 轮询正常工作
- [ ] bot 完整跑通，在真实群里收发消息
- [ ] 旧文件已备份（.bak）

## Blocked by

- #5 Pipeline 编排 + 入口

## Parent

PRD: `docs/superpowers/specs/2026-06-24-bot-refactor-PRD-final.md`
