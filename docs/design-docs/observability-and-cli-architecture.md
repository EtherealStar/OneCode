# Observability And CLI Architecture

本文描述 `services/observability/` 与 `ui/cli/` 的架构。可观测性是 runtime 事实来源，CLI 是当前用户界面实现。

## Observability

`services/observability/` 负责结构化 runtime trace。

当前模块：

- `events.py`：定义 `TraceRecord`。
- `trace.py`：实现 `TraceRecorder` 和 span helper。
- `sinks.py`：实现 JSONL sink 和 noop sink。
- `sanitize.py`：清洗 trace metadata，避免记录 prompt 全文、源码全文或工具输出全文。

`TraceRecorder` 支持：

- `event(name, attributes)`
- `span(name, attributes)`
- `flush()`
- `switch_session(session_id)`
- `recent_records(limit)`

JSONL trace 写入 `.onecode/<session_id>/trace.jsonl`。

## 当前 Trace 事件

loop 发布：

- `interaction`
- `context_prepare`
- `model_call`
- `model_call_error`
- `transition`

executor 发布：

- `tool_batch`
- `tool_preflight`
- `permission_wait`
- `tool_execution`
- `tool_result`

hook registry 发布 hook span。

subagent runner 发布：

- `subagent_start`
- `subagent_completed`
- `subagent_error`

trace 只记录摘要 metadata，不记录完整 prompt、源码或工具输出。它应该成为 CLI、测试、回放和未来 UI 共享的事实来源，而不是普通 debug 文本。

## CLI

`ui/cli/` 是 OneCode 当前的标准库交互界面。可通过：

```text
uv run python -m ui.cli.app
```

启动。

CLI 是 UI，不是 runtime。它负责应用装配、交互输入、命令处理、权限提示和终端渲染，但不实现 agent 主循环、工具执行、安全策略或 provider 协议。

## CLI 装配

`ui/cli/app.py` 的 `build_runtime(workspace)` 当前装配：

- `RuntimeState`
- `MessageStore`
- `SessionPermissionStore`
- `PermissionPolicy`
- `JsonlTraceSink`
- `TraceRecorder`
- base descriptors：`read_file`、`edit_file`、`glob`、`grep`、`bash`
- `ToolRegistry`
- `DynamicPromptAssembler`
- `ContextEngine`
- `SandboxGuard`
- `CliPermissionPrompter`
- `HookRegistry`
- `CurrentModelContext`
- provider model client
- `SubagentRunner`
- `agent` descriptor
- `RegistryToolExecutor`
- `AgentLoop`

`CliRuntime` 将这些组件集中保存，供 slash commands 和 render 层使用。

## CLI Commands

`ui/cli/commands.py` 当前支持：

- `/help`
- `/tools`
- `/status`
- `/history [n]`
- `/trace [n]`
- `/resume <session-id-or-messages.jsonl>`
- `/clear`
- `/exit`
- `/quit`

`/resume` 可以从 `.onecode/<session_id>/messages.jsonl` 或显式 JSONL 路径恢复当前会话。恢复会创建新的 `RuntimeState` 和 `MessageStore`，并通过 `CliRuntime.with_session()` 重新绑定相关 session 组件。

`/clear` 会 flush 当前 transcript，开启新 session，清空消息链、trace session 和 session permission grants。

## Rendering And Permissions

`ui/cli/renderer.py` 负责把 banner、状态、工具列表、历史摘要、trace 摘要、assistant delta、工具结果摘要和错误渲染为终端文本。

`ui/cli/permissions.py` 实现 `CliPermissionPrompter`。当 `PermissionPolicy` 返回 ask 时，executor 会等待 CLI 权限确认；用户可以 allow once、allow session directory 或 deny。

## 当前限制

CLI 当前已经支持 streaming token 渲染、工具结果摘要、async 权限交互、JSONL transcript 恢复和 trace 查看，但仍缺少：

- provider recovery UI。
- context compact 状态展示。
- `/compact`。
- provider connect/model selection flow。
- 实时 trace 订阅 UI。
- debug log 与 trace 的更完整分离策略。

这些能力应在对应 runtime service 落地后接入 CLI，而不是由 CLI 直接实现 provider-specific 或 recovery-specific 分支。
