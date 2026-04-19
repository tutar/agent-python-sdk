# Feishu Cards And Streaming Replies

Status: completed

## Summary

将 Feishu channel 中当前依赖 slash 命令的交互改为飞书卡片按钮，并让 agent 回复通过飞书卡片流式更新。

## Scope

- 仅影响 `feishu` channel
- 不改变 terminal/TUI
- 不要求其他 channel 复用卡片交互模型
- WeChat 等未来 channel 如果不支持卡片，不受此 proposal 约束

## Current State

- Feishu 控制流此前依赖 slash 文本命令
- 当前 `/channel` 与 `/channel-config` 仍保留为暂存的 management 路径
- reply card 与 card action 现已落地
- reaction 已支持轻量状态提示

## Proposed Design

- 在 Feishu 中将控制命令替换为交互卡片按钮
- `/channel` 与 `/channel-config` 不在本次实现范围，后续转到 host management page
- 普通 agent 回复通过单 turn reply card 承载，并通过 Feishu CardKit streaming updates 形成流式体验
- reaction 保留为轻量状态提示，但不承担命令交互职责

## Replaced Feishu Slash Commands

- `/approve`
- `/reject`

## Interaction Model

- 需要人工操作时，Feishu host 发送交互卡片
- 卡片按钮回传 action payload
- host 将 action payload 映射为现有 canonical control intent
- 普通回复开始时创建卡片，生成中持续更新，完成后进入稳定终态

## Required States

- thinking / running
- requires_action
- completed
- failed
- interrupted

## Host / Gateway Boundary

- Gateway 继续处理 canonical control / management intent
- Feishu host 负责 card action ingress
- Feishu host 负责 card create / update / finalize
- 这套 card 交互不提升为跨 channel 通用契约

## Delivered Behavior

- 普通 Feishu turn 现在默认创建单 turn reply card，并优先通过 CardKit streaming updates 更新同一张卡；如果租户权限或平台能力不足，会自动降级为对同一张消息卡片做 patch 更新
- `requires_action` 通过卡片按钮触发 `approve/reject`
- 审批卡片按钮只包含 `Approve` / `Reject`
- `interrupt` 与 `resume` 继续保留为主动控制语义，不承载在审批卡片按钮中
- card action 默认通过飞书长连接事件进入 host
- 卡片发送/更新失败会进入 file-backed retry queue
- pending card 重试按当前 `conversation_id` 隔离
- 在补发成功前，原消息保持 `OneSecond` reaction；成功后切 `DONE`
- reply card 的 CardKit 跟踪标识以 `card_id + uuid + sequence` 维护，不再依赖 `im.v1.message.update`

## 参考
- 卡片按钮：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/interactive-components/button
- 流式更新卡片：https://open.feishu.cn/document/cardkit-v1/streaming-updates-openapi-overview?lang=zh-CN
