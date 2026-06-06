# Subagents Architecture

本文描述 `services/subagents/` 与 `tools/agent/` 的架构。subagent 机制通过普通工具接入父 agent，不在 `core/loop.py` 中增加工具名分支。

## 模块职责

`services/subagents/types.py` 定义 subagent 数据结构：

- `AgentDefinition`
- `SubagentRequest`
- `SubagentResult`

`services/subagents/definitions.py` 定义内置 agent。

`services/subagents/forking.py` 构造 fork child 的消息链。

`services/context/current_model_context.py` 保存当前父模型调用的 `ContextSnapshot`，供 fork child 继承父 prompt 字符串。该 holder 位于通用 context service 边界，避免 `core.loop` 依赖 subagent 包。

`services/subagents/runner.py` 装配 child runtime，并同步 drain child loop 得到最终摘要。

`tools/agent/tool.py` 将 `SubagentRunner` 包装成普通工具 descriptor。

## 内置 Agent

当前内置定义：

- `general-purpose`：干净上下文的复杂研究或多步分析。
- `Explore`：只读代码探索。
- `Plan`：只读实现规划。
- `fork`：隐藏 synthetic agent；当 `subagent_type` 省略时使用。

`Explore` 和 `Plan` 标记为 read-only，并禁用 `agent`、`edit_file`、`write_file`。

## 调用模式

父 agent 只看到 `agent` 工具调用。

显式 `subagent_type`：

- child 使用干净消息链。
- child 第一条消息是用户传入的 prompt。
- child 使用对应 `AgentDefinition.system_prompt`。

省略 `subagent_type`：

- 触发 fork。
- child 复制父消息链，并追加 fork prompt。
- child 继承父轮次已经渲染的 `ContextSnapshot.system_prompt` 字符串。

fork 的目标是复用父上下文和 prompt 字节，而不是让 child 重新组装一套可能不同的 system prompt。

## Child Runtime 装配

`SubagentRunner` 为每次调用创建新的：

- `RuntimeState`
- `MessageStore`
- `ToolRegistry`
- `ContextEngine`
- `RegistryToolExecutor`
- `AgentLoop`

child 共享父级：

- workspace
- transcript root
- model client
- sandbox guard
- permission policy
- permission prompter
- trace recorder
- base descriptors

child 的中间消息写入 child transcript，不写回父 `MessageStore`。父链只收到 `agent` 工具的最终 `ToolExecutionResult`。

## 工具裁剪

所有 child 都隐藏 `agent` 工具，避免递归 subagent。

child registry 基于 `AgentDefinition.tools` 和 `disallowed_tools` 裁剪 base descriptors。若 definition 标记 read-only，`RuntimeState.metadata["read_only_agent"] = True`，permission policy 会硬性 deny 非只读或修改文件系统的工具调用。

这意味着只读限制由权限层强制，不只是 prompt 约束。

内部 runtime 任务可以通过 `SubagentRequest.metadata["purpose"]` 进入更窄的 fork mode。当前已落地 `purpose="session_memory_extraction"`：它仍使用 fork 消息链和父 prompt 字符串，但 child registry 只暴露 `edit_file`，child state 写入 `memory_extraction_agent=True` 和 `allowed_memory_path=<session-memory.md>`。`PermissionPolicy` 会拒绝任何非 `edit_file` 工具、任何非文件写入目标，以及任何不等于 `allowed_memory_path` 的编辑。这个限制是代码边界，不依赖 prompt 文本。

## Trace

subagent runner 写入：

- `subagent_start`
- `subagent_completed`
- `subagent_error`

trace metadata 包含 parent session、child session、agent type、是否 fork、是否 read-only、usage、tool result count 和 duration 等摘要信息。

## 当前限制

当前 subagent 是同步运行的 child loop，尚未支持：

- background task lifecycle。
- child worktree 隔离。
- 用户、项目或插件目录加载自定义 agent。
- 持久 agent catalog。
- 完整 prompt-cache identical fork 参数校验。

后续扩展仍应保持 `agent` tool descriptor 和 `SubagentRunner` 作为接入边界，不应在主循环中添加 subagent 特例。
