# Session

`session/` 当前只负责 session durable state 与 short-term continuity，不再承载 durable memory。

## 当前支持

- `InMemorySessionStore`
- `FileSessionStore`
- `InMemoryShortTermMemoryStore`
- `FileShortTermMemoryStore`
- append-only event log baseline
- session checkpoint / cursor baseline
- wake / resume snapshot baseline
- working-state restore inputs
- single active harness lease
- short-term memory safe-point update and stabilization
- terminal TUI 的多 session 切换与 replay

## 当前不支持

- richer branch / sidechain transcript graph
- richer short-term salience / eviction policy
- more explicit restore mode matrix
