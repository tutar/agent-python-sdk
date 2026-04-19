# Feishu Host Management Page

Status: proposed

## Summary

将 Feishu 中当前仍保留的 host management 文本命令迁移到专门的 management page 或等价 UI，而不是继续依赖聊天输入：

- `/channel`
- `/channel-config`

## Current State

- Feishu 已切到 reply cards + card action 控制流
- 控制命令 `/approve`、`/reject`、`/interrupt`、`/resume` 已不再依赖文本 slash
- 但 host management 仍通过文本命令操作

## Proposed Design

- 引入 Feishu 专用的 host management page 或等价的可视化配置入口
- 将 channel 加载状态和 Feishu runtime config 放到该页面中管理
- Feishu 聊天窗口不再承载 `/channel`、`/channel-config` 这类管理指令

## Notes

- 这项能力只影响 `feishu` channel
- 不要求 terminal/TUI 或未来其他 channel 复用同样的 management page 设计
