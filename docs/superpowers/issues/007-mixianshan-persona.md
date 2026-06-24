# Issue 7: 重写 system_prompt——米线山人设

## What to build

把 bot 的 system prompt 从孙吧老哥改为米线山（串子大王）。只改 `config.yaml` 中 `bot.system_prompt` 字段，零代码改动。

## Acceptance criteria

- [ ] system_prompt 包含"米线山"身份，不包含"孙笑川吧"、"孙吧"
- [ ] 回复规则：2-5 句话、一条完整消息不拆段、用"你"不用"宁"
- [ ] 风格描述：接梗快、反转不意外、损友不喷子、群里串子多别站队
- [ ] bot 名称说明：群友叫你"鼠鼠"，这是群里的称呼，不用纠正
- [ ] `tests/test_prompt.py` 新增一个测试验证 prompt 包含关键新词汇
- [ ] 旧 prompt 的嘴臭元素全部移除（"宁"、"贵物"、"绷不住了"、"骂完就跑"）

## Blocked by

None — 可以立即开始

## Parent

PRD: `docs/superpowers/specs/2026-06-24-bot-persona-mixianshan.md`
