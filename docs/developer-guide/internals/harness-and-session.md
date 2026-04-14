# Harness And Session

`harness` 和 `session` 共同负责 turn execution 和状态保持。

简单说：

- `harness` 决定“一次 turn 怎么跑”
- `session` 决定“跑完以后状态怎么存”

## Harness Role

当前核心实现是 `SimpleHarness`。

它的职责是：

1. 读取当前 session
2. 写入用户消息
3. 构造 model input
4. 调用 model adapter
5. 处理 assistant message 或 tool calls
6. 在需要时进入 requires_action
7. 把结果写回 session 和 event log

除了单次 `generate(...)` 外，当前 harness 还支持流式模型：

- model 可实现 `stream_generate(...)`
- harness 会把流式输出先映射成 `assistant_delta`
- 最终再汇总成 `assistant_message`

## Turn Lifecycle

当前一次 turn 的典型事件顺序是：

1. `turn_started`
2. `assistant_message` 或 `tool_started`
3. `tool_result` 或 `requires_action`
4. `turn_completed` 或 `turn_failed`

如果 tool 需要审批：

1. 当前 turn 先停在 `requires_action`
2. pending tool call 存进 session
3. 后续通过 `continue_turn(...)` 恢复

当前 turn 控制还额外支持：

- cooperative cancellation
- timeout baseline
- retry baseline

这些控制通过 `TurnControl` 传入 harness，而不是散落成多个布尔参数。

## Context Governance Integration

当前 `build_model_input(...)` 会在真正构造模型输入前做治理判断：

1. 先分析当前消息预算
2. 接近阈值时做 proactive compact
3. 已经超预算时走 overflow recovery
4. 生成 continuation budget plan
5. 生成 prompt-cache-aware plan
6. 把结果写入 `last_context_report`

这意味着治理结果是可观测的，而不是只体现在 message 数量变化上。

当前 `last_context_report` 还会显式暴露：

- `continuation_message_budget`
- `recommended_max_output_tokens`
- `provider_cache_key`

`ContextGovernance` 现在还支持结构化 prompt-cache snapshot 和 break detection：

- stable prefix snapshot
- dynamic suffix snapshot
- break reason classification
- fork child cache-sharing
- strategy-equivalent upper-layer semantics

## Why Session Is Separate

session 不放进 harness 内部，是为了把执行和状态持久化拆开。

这样可以让同一个 harness 适配：

- `InMemorySessionStore`
- `FileSessionStore`

同时也方便 replay 和 checkpoint。

## Session Model

当前 session 层包含：

- `SessionRecord`
- `SessionMessage`
- `SessionCheckpoint`
- `SessionCursor`
- `WakeRequest`
- `ResumeSnapshot`
- `SessionStatus`

`SessionRecord` 当前既保存：

- 消息历史
- pending tool calls
- 生命周期状态
- event index / checkpoint 相关状态
- restore marker

durable memory 不保存在 `SessionRecord` 里。

这是刻意的分层：

- transcript / session state 负责当前会话历史
- durable memory 负责可被后续 turn recall 的长期信息

## Event Log Strategy

当前 session store 已经不是单纯“覆盖式保存”。

它同时支持：

- session snapshot
- append-only event log
- read_events(...)
- checkpoint readback
- cursor-based replay
- resume snapshot
- restore marker persistence

这个设计让 terminal TUI 可以在切换 session 时重放历史事件，而不是只看到最新状态。

## Current Tradeoff

当前 session 层的取舍是：

- 优先本地简单性
- 优先可 replay
- 暂不做复杂恢复协议

还没补齐的部分包括：

- 更强的 side-state recovery guarantee
- 分支化 event log
- 更完整的 wake/restore mode 矩阵
