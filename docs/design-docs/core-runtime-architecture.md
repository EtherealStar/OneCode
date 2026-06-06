# Core Runtime Architecture

本文描述 `core/` 的架构边界。`core/` 是 OneCode agent runtime 的编排层，只表达生命周期、状态和 transition，不承载具体工具、provider、安全策略、prompt 文本或 UI 行为。

## 模块职责

`core/loop.py` 定义 `AgentLoop`，是当前 runtime 的薄主循环。它负责接收用户输入、追加 user message、每轮重建上下文、调用模型、写入 assistant message、执行模型实际请求的工具调用、写回 tool results，并在没有工具调用时完成任务。

`core/context_engine.py` 定义 `ContextEngine` 和三个可注入协议：`ContextPreparer`、`PromptAssembler`、`ToolSchemaProvider`。它是模型调用前的上下文重建边界。

`core/runtime_state.py` 定义 `RuntimeState`，保存单个会话的 usage、turn count、max turns、session id、last transition 和 metadata。

`core/transitions.py` 定义 provider-neutral 的 transition reason。当前 loop 已消费 `tool_use`、`completed` 和 `max_turns`；rate limit、reactive compact、max output recovery 和 stop hook continue 仍是目标恢复能力。

`core/stream_events.py` 定义 loop 向 UI 或调用方输出的 `AgentEvent`，使 CLI 能消费 streaming assistant delta、工具事件、transition 和 completed 结果。

## AgentLoop

`AgentLoop.stream(prompt, attachments=None)` 是普通用户交互入口。它会先把 prompt 追加到 `MessageStore`，再追加调用方预构建的 durable attachment messages，然后进入 `_run_loop_async()`。

附件解析、文件读取、权限检查和投影不属于主循环。CLI 或其他入口负责在调用 loop 前收集附件；`ContextEngine` 的 preparer 负责在模型调用前把 internal attachment role 投影成 provider-visible messages。

`AgentLoop.continue_stream()` 用于子 agent 或恢复场景，从已经 seed 到 `MessageStore` 的消息链继续运行，不重复追加用户 prompt。

当前主循环行为：

1. 递增 `RuntimeState.turn_count`。
2. 超过 `max_turns` 时设置 `TransitionReason.MAX_TURNS` 并返回停止文本。
3. 调用 `ContextEngine.build_for_model(state)`。
4. 调用 `ModelClient.stream(snapshot)` 并向外转发 assistant delta 和 tool call ready 事件。
5. 等待 provider-neutral `message_completed` 事件。
6. 累计 usage，追加 assistant message。
7. 如果 `message_completed.metadata["tool_calls"]` 中存在实际工具调用，则执行工具并进入下一轮。
8. 如果没有工具调用，则设置 `completed` transition 并返回 final text。

主循环判断是否继续时只看实际 tool calls，不依赖 provider 私有 `stop_reason`。

## ContextEngine

`ContextEngine` 当前从 `MessageStore.current_messages()` 读取内部消息副本，经过 `ContextPreparer`，再调用 `PromptAssembler` 和 `ToolSchemaProvider` 生成 `ContextSnapshot`。

默认实现：

- `NoOpContextPreparer`：透传消息，是未来 compaction/projector 的接入点。
- `DynamicPromptAssembler(Path.cwd())`：默认生成非空 system prompt。
- `EmptyToolSchemaProvider`：测试或最小装配时不暴露工具。

CLI 装配会显式传入 `DynamicPromptAssembler(workspace, tool_registry=registry)` 和 `ToolRegistry`，因此真实运行时的 prompt 和 tool schema 都来自当前可见工具视图。

## RuntimeState

`RuntimeState` 是会话级可变状态。稳定字段包括：

- `usage`：累计模型 usage。
- `turn_count` 与 `max_turns`：主循环轮次控制。
- `has_attempted_reactive_compact`：目标 reactive compact 状态。
- `max_output_recovery_count`：目标 max output recovery 状态。
- `last_transition`：上一轮 transition。
- `session_id`：当前会话 UUID。
- `metadata`：运行期扩展事实。

当前 metadata 已用于：

- `files_read`：executor 在 `read_file` / `edit_file` 成功后记录规范化路径，供 `edit_file` 的 read-before-edit 规则使用。
- `disabled_tools`、`denied_tools`、`hidden_tools`：工具可见性裁剪。
- `read_only_agent`：只读 subagent 的硬性权限限制。
- `is_fork_child`：fork child 标记。

`start_new_session()` 会重置消息相关状态、usage、transition 和 metadata，但保留 `max_turns` 作为运行时配置。

## Transition

当前代码中的 transition 名称是稳定 runtime 词汇：

- `tool_use`
- `completed`
- `max_turns`
- `rate_limit_retry`
- `reactive_compact_retry`
- `max_output_tokens_escalate`
- `max_output_tokens_recovery`
- `stop_hook_continue`

恢复类 transition 已定义但尚未完整接入 loop。后续 provider retry、context limit、output interruption 和 stop hook 行为应映射为这些 transition，并通过 trace 和 tests 验证。

## Core 不负责的内容

`core/` 不应实现：

- 具体工具逻辑或工具名分支。
- provider wire protocol、HTTP、API key 或模型 catalog。
- 路径规范化、sandbox 分类或权限 UI。
- system prompt 的具体 section 文本。
- transcript 文件格式和 trace 文件格式。
- CLI slash commands 或渲染。

这些能力分别属于 `tools/`、`infrastructure/`、`services/guard`、`services/permissions`、`prompts/`、`services/context`、`services/observability` 和 `ui/cli`。
