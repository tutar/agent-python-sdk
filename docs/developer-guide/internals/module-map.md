# Module Map

这份文档把 `agent-spec` 的目录边界翻译成当前 `openagent` 的实现参考。

它的目标不是重复规范正文，而是回答三件事：

- `agent-spec` 的模块拆分逻辑是什么
- 当前 `src/openagent` 分别对应到哪里
- 后续目录调整应该按什么规则推进

## Overview

`agent-spec` 当前按 5 个核心模块组织：

- `harness`
- `session`
- `tools`
- `sandbox`
- `orchestration`

共享规范层另外单列：

- `object-model`
- `capability-surface`
- `conformance`
- `terminology-and-ownership`

对 `openagent` 来说，这意味着目录结构调整应优先围绕“模块职责”而不是“单文件长度”推进。

## `agent-spec` Directory Map

### Top-Level Modules

- `harness/`
  - turn runtime
  - context assembly
  - model provider adapter
  - gateway / channel adapter 接入边界
  - runtime event / observability projection seam
- `session/`
  - transcript
  - event log
  - checkpoint / cursor / resume
  - short-term memory
  - `session.memory` linkage
- `tools/`
  - tool-model
  - command-surface
  - builtin tools
  - skills
  - mcp
- `sandbox/`
  - execution sandbox
  - environment sandbox
  - capability model
  - security boundary
- `orchestration/`
  - task lifecycle
  - background agent
  - verifier / reflection task
  - local / cloud orchestration profile

### `harness/` Subtopics

`agent-spec/harness/` 还继续按 5 个子主题拆分：

- `runtime-core/`
  - turn loop
  - terminal / failure state
  - runtime state layering
- `model-provider/`
  - provider capability routing
  - provider adapter
  - streaming adapter
- `context-assembly/`
  - bootstrap prompts
  - context providers
  - context governance
  - prompt cache strategy
- `gateway/`
  - gateway
  - channel adapter
  - interaction loop
  - bridge transport / projection
  - local session adapter
- `extension-and-projection/`
  - observability
  - hooks
  - post-turn processing
  - event transport

### `tools/` Subtopics

`agent-spec/tools/` 当前继续拆成：

- `tool-model/`
- `command-surface/`
- `builtin/`
- `skills/`
- `mcp/`

这代表 `tools` 不是单一 registry/executor 文件夹，而是一个能力域。

## Current `openagent` Mapping

### Already Aligned

- `object_model/`
  - 对应共享 canonical object model
- `harness/`
  - 已承载 runtime、provider、bootstrap、model I/O
- `session/`
  - 已承载 transcript、store、checkpoint、short-term memory
- `tools/`
  - 已承载 builtin / commands / skills / mcp / executor / policy
- `sandbox/`
  - 已形成独立模块
- `orchestration/`
  - 已形成 task manager / local background baseline
- `gateway/`
  - 当前作为 harness 域下的 frontend integration boundary 存在

### Partially Aligned Or Mixed

- `local.py`
  - 当前是本地装配 facade
  - 混合了 runtime assembly、gateway assembly、workspace/model-io 路径策略
- `capability_surface.py`
  - 当前同时包含对象模型、filtering、host projection、lookup 逻辑
  - 更像 shared surface seam，而不是单纯顶层工具函数
- `context_governance.py`
  - 语义上属于 `harness/context-assembly`
  - 但当前仍停在顶层，并混合：
    - 数据模型
    - budget/cache 治理
    - tool result externalization

## Refactor Rules

后续调整 `src/openagent` 目录时，默认遵守这些规则：

### 1. 先按模块归属，再按文件大小

- 文件长不是拆分理由本身
- 真正的拆分依据是职责是否跨域

### 2. 顶层只保留共享层和稳定入口

`src/openagent/` 顶层应尽量只保留：

- facade / re-export
- truly shared modules
- 极少数明显的 top-level entry helpers

不应继续把领域实现长期停在顶层。

### 3. `gateway` 视为 harness 域内边界

- `gateway` 不是第六个顶层核心模块
- 它属于 harness 域下的 frontend / channel integration boundary
- 目录上可以保留为 `openagent/gateway/`，但职责判断要按 harness 语义来做

### 4. `tools` 是能力域，不是单文件执行器

- `commands`
- `skills`
- `mcp`
- `web backend`

都应继续被视为 `tools` 域的稳定子面，而不是外溢成新的顶层域。

### 5. 允许 facade，优先保持 public imports 稳定

后续目录下沉时：

- 可以保留旧顶层 re-export
- 优先避免打破 `openagent.__init__` 和已公开导出
- 真正变的是内部归属和实现路径

## Recent Refactor Follow-Through

这三项 Immediate Follow-Ups 已经按模块职责落地：

### `local.py`

现在已经收成稳定的 local assembly facade：

- 顶层 `local.py` 只保留公开 helper
- 真实装配逻辑下沉到 `harness/assemblies/local_runtime.py`
- workspace / model-io root 默认值和 runtime wiring 不再继续堆在顶层

### `capability_surface`

现在已经从单文件拆成 shared package：

- `capability_surface/models.py`
- `capability_surface/projection.py`
- `capability_surface/surface.py`

这层继续保留顶层导入语义，但内部已经明确分开：

- object model
- host projection / filtering
- high-level surface facade

### `context_governance`

现在已经按 `harness/context-assembly` 归位到 `harness/context/`：

- `models.py`
- `prompt_cache.py`
- `tool_result.py`
- `governance.py`

顶层 `context_governance.py` 只保留兼容 re-export，不再承载真实实现。

## Recommended Reading Order

如果后续要调整 `src/openagent` 目录结构，建议按这个顺序看：

1. `agent-spec/module-overview.md`
2. `agent-spec/README.md`
3. `agent-spec/harness/README.md`
4. `agent-spec/session/README.md`
5. `agent-spec/tools/README.md`
6. 本文档
7. `docs/developer-guide/architecture.md`

这样调整时会先遵守模块边界，再进入具体实现重构。
