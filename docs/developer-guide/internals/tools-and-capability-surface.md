# Tools And Capability Surface

这一层负责两件事：

- 执行可调用能力
- 把这些能力统一投影给 runtime 或 frontend

## Tool Execution

当前 tools 主体由三部分组成：

- `StaticToolRegistry`
- `SimpleToolExecutor`
- `ToolCall / ToolExecutionContext`

registry 解决“有哪些 tool”。

executor 解决“怎么执行这些 tool”。

当前本地事件流是：

- `tool_started`
- `tool_progress`
- `tool_result`
- `tool_failed`
- `tool_cancelled`

这里的边界很明确：

- executor 负责生成 tool lifecycle 事件
- harness 负责把这些事件写入 session event log
- harness 再把 `tool_failed / tool_cancelled` 转成对应的 turn 终态

## Permission Flow

每个 tool 当前通过 `check_permissions(...)` 返回：

- `allow`
- `deny`
- `ask`

当返回 `ask` 时，executor 不直接执行，而是抛出 `RequiresActionError`。

上层 `SimpleHarness` 捕获后，把它写成：

- `requires_action` runtime event
- pending tool calls in session

这就是 terminal TUI 审批流的基础。

当前还提供了 `RuleBasedToolPolicyEngine`：

- 可以按 `tool_name`
- 或按 `session_id_prefix`
- 覆盖默认 `allow / deny / ask`

如果规则未命中，仍可回退到 tool 自身的权限策略。

## Concurrency Strategy

当前 executor 会区分 tool 是否 concurrency-safe：

- concurrency-safe：可并发执行
- 非 concurrency-safe：串行执行

这不是完整调度系统，但已经形成了最小调度语义。

## Commands / Skills / MCP

当前 SDK 里不只有 tool。

还存在三类相关能力：

- `Command`
- `SkillDefinition`
- MCP-derived capability

它们的作用不同：

- tool：偏执行
- command：偏 prompt / local UI action
- skill：偏可复用 prompt 能力

MCP 当前只是内存兼容层，不是 transport-backed 实现。
现在已经补上 `TransportBackedMcpClient + McpTransport` seam，默认测试 transport 仍然是
`InMemoryMcpTransport`。这保证 adapter 和 capability surface 可以先对 transport 抽象编程，
而不是绑定在单一的内存客户端上。

## Policy Engine Seam

executor 现在支持一个可选的 `ToolPolicyEngine`：

- 默认路径仍然走 tool 自己的 `check_permissions(...)`
- 如果注入 policy engine，则由 engine 给出最终 `allow / deny / ask`
- approval continuation 语义保持不变

这让更复杂的宿主策略可以落在 executor 边界，而不需要把 tool 定义层改成 host-aware。

## Capability Surface

`CapabilitySurface` 的目标是把不同来源的能力统一成一个投影视图。

当前可以统一投影：

- tools
- commands
- skills

并补上：

- origin metadata
- model-visible / user-visible
- host projection

这样 host 层就不需要分别理解 command、skill、tool 的所有细节。

## Why This Matters

如果没有 capability surface，frontend 或 host 层会直接耦合到多个 registry：

- tool registry
- command registry
- skill registry

这会导致展示和过滤逻辑分散。

统一投影之后，host 只需要做：

- list
- filter
- resolve
- project

## Current Limitation

当前这层还没补齐：

- richer policy engine implementation
- 更细的 capability origin lineage
