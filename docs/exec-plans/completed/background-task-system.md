# OneCode 后台任务执行系统

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。实现者只阅读本文件和当前工作树，也应能完成后台任务执行系统第一版，不需要依赖此前对话。

## Purpose / Big Picture

完成本计划后，OneCode 可以把长时间运行的本地 bash 命令、subagent 调用和自动长期记忆整理放到后台执行。用户可以让模型用 `bash` 的 `run_in_background` 启动慢命令，或者用 `agent` 的 `run_in_background` 启动后台 local agent；当前 agent 不再等待这些慢操作结束，而是立即得到一个后台任务 ID 和输出文件路径。后台 bash 和后台 local agent 完成后，通知会进入内存队列，并在用户下一次输入时由现有 `AttachmentCollector.collect_for_user_turn()` drain 到模型上下文。长期记忆的 `dream` 任务是内部可视化任务：它在 turn 自然停止后自动启动，运行现有受限长期记忆提取逻辑，但不向模型注入 `<task_notification>`。

可观察结果是：`/tools` 中能看到 `bash` 和 `agent` 接受 `run_in_background` 参数，以及新增 `background_task_stop` 工具；运行 `bash` 后台命令会立即返回 `b_...` 任务 ID，输出写入 `.onecode/<session_id>/background-tasks/<task_id>.output`；命令结束后，下一次用户输入会把 `<task_notification>` 作为 runtime attachment 投影给模型；`/status` 和新增 `/background-tasks` 能看到运行中和终端后台任务；长期记忆自动提取不再阻塞 turn 结束，而是显示为 `d_...` dream 任务。

第一版只实现 `local_bash`、`local_agent` 和 `dream`。不实现 `remote_agent`、`in_process_teammate`、`local_workflow`、`monitor_mcp`，不实现 `TaskOutputTool`，不实现后台任务完成后主动唤醒 agent 跑一轮，不实现 UI 面板或 React/Ink 风格任务视图。

## Progress

- [x] (2026-06-07 23:05+08:00) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、相关 design docs、active exec plans、tech debt tracker、`docs/references/s13_background_tasks/`、当前 `services/tasks` Todo task system、`tools/bash`、`tools/agent`、`services/subagents`、`services/memory/extraction.py`、attachment queue 预留接口和 CLI 装配。
- [x] (2026-06-07 23:20+08:00) 与用户确认第一版范围：`bash` 参考 `local_bash` 增加后台能力；`agent` 增加后台能力；后台通知只在下一次用户输入时 drain；输出文件放在推荐的 session-local `.onecode/<session_id>/background-tasks/`；不实现 `TaskOutputTool`；停止工具命名为 `background_task_stop`；dream 是 turn 停止后自动触发的后台长期记忆可视化任务；dream 不注入 `<task_notification>`；dream 运行时用户下一次输入允许使用旧 memory。
- [x] (2026-06-07 23:35+08:00) 撰写本中文 ExecPlan，固化架构落点、接口、实现顺序、测试策略、参考实现行为和第一版取舍。
- [x] (2026-06-07 23:55+08:00) 实现 `services/background_tasks/`，包括后台任务 ID、状态、输出文件路径、进程内 manager、完成通知 drain，以及 `BackgroundTaskNotificationSource`。
- [x] (2026-06-07 23:58+08:00) 扩展 CLI 装配、banner、help、`/status` 和新增 `/background-tasks`，将后台通知 source 接入 `AttachmentCollector`，并把通知投影为 `<task_notification>`。
- [x] (2026-06-07 23:59+08:00) 扩展 `bash(run_in_background=true)`、`agent(run_in_background=true)`，新增 `background_task_stop` 工具；后台 local agent 通过 request metadata 禁用交互式 permission prompter。
- [x] (2026-06-07 23:59+08:00) 将长期记忆自动提取拆成 prepare/run job，并在 `TURN_STOPPED` hook 中启动不通知模型的 `dream` background task。
- [x] (2026-06-07 23:59+08:00) 补充后台任务 manager、bash、agent、attachment projector、CLI 和长期记忆 prepare job 测试；运行 compileall 和全量 pytest，结果为 `300 passed`。

## Surprises & Discoveries

- Observation: 当前 OneCode 没有 Claude Code 参考实现那种完整 `messageQueueManager`，但已有可复用的 queued attachment 通道。
  Evidence: `services/attachments/collector.py` 定义 `QueuedAttachmentSource.collect(state)`；`services/attachments/projector.py` 已能投影 `queued_command`；`ui/cli/app.py` 创建 `AttachmentCollector` 时 `shared_sources` 仍为空。因此第一版应新增后台通知 source 并接入 `shared_sources`，不需要在主循环中硬编码后台任务通知。

- Observation: 当前长期记忆机制已经为 dream/background 化预留了主要边界。
  Evidence: `services/memory/extraction.py::LongTermMemoryExtractionService` 通过 `SubagentRequest(metadata={"purpose": "long_term_memory_extraction"})` 运行受限 fork child；`services/subagents/runner.py` 识别该 purpose；`services/permissions/policy.py` 有 `long_term_memory_extraction_agent` 权限分支；`docs/exec-plans/active/long-term-memory.md` 明确说明第一版阻塞、后续应单独设计 background extraction 和 Dream consolidation。

- Observation: 参考实现中的 LocalAgentTask 本身不实现独立权限系统。
  Evidence: `docs/references/s13_background_tasks/tasks/LocalAgentTask/LocalAgentTask.tsx` 负责注册 `LocalAgentTaskState`、`AbortController`、输出文件 symlink、progress、kill 和通知；真正工具权限仍由 agent 执行上下文和工具系统处理。Claude Code 有统一 TUI/AppState 可以承载后台 agent 期间的交互状态，而当前 OneCode CLI 的 `CliPermissionPrompter` 会调用 `input()`，不能安全地在后台任务中同时抢占终端输入。

- Observation: `RegistryToolExecutor` 已经支持当 permission decision 为 `ask` 且没有 `permission_prompter` 时返回结构化 `permission_ask_required` 工具错误。
  Evidence: `services/tools/executor.py` 的 permission 处理会在 `_permission_prompter is None` 时返回 `_permission_ask_required_result()`；这使后台 local_agent 可以复用现有 permission policy 和已有 session/project 授权，但第一版不在后台弹交互式权限 prompt。

- Observation: 后台任务可以不改 `core/loop.py` 落地，现有工具 descriptor、hook 和 attachment collector 边界足够承载第一版。
  Evidence: 本次实现只改 `services/background_tasks/`、具体工具、CLI 装配、attachment projector 和长期记忆 service；全量 `uv run python -m pytest tests -q` 通过，且 `core/loop.py` 没有新增后台任务或工具名分支。

- Observation: 原有 `bash` 前台 handler 的测试能及时捕捉后台路径插入时对同步返回路径的破坏。
  Evidence: 第一次定向测试中 `tests/test_bash_tool.py::test_bash_handler_uses_runner_and_interprets_no_match` 失败，原因是前台 result formatting 块被误放到后台 helper 的 unreachable 位置；修复后相关定向测试 `41 passed`。

## Decision Log

- Decision: 后台任务系统新增为 `services/background_tasks/`，不复用 `services/tasks/`。
  Rationale: `services/tasks/` 是磁盘持久 Todo 工作项系统，ID 是数字，状态是 `pending/in_progress/completed`，工具是 `task_create/task_get/task_update/task_list`。后台任务是进程内异步执行生命周期，ID 是随机前缀，状态是 `pending/running/completed/failed/killed`，输出写文件，支持 stop。两个系统语义不同，混在同一 store 会让模型和代码都难以判断“task”到底指什么。
  Date/Author: 2026-06-07 / Codex

- Decision: 第一版只实现 `local_bash`、`local_agent` 和 `dream`。
  Rationale: 用户明确不需要 `remote_agent`、`in_process_teammate`、`local_workflow` 和 `monitor_mcp`。这些类型需要远程会话、协作面板、脚本 feature gate 或 MCP 监控生命周期，不应进入第一版。
  Date/Author: 2026-06-07 / Codex

- Decision: `bash` 工具增加 `run_in_background` 参数，参考 `local_bash` 实现后台本地命令。
  Rationale: Claude Code 的 bash 工具 schema 使用 `run_in_background` 显式请求后台执行。OneCode 已有 `tools/bash/tool.py` 作为唯一 bash descriptor，扩展它能保持模型入口一致，也避免新增重复 shell 工具。
  Date/Author: 2026-06-07 / Codex

- Decision: `agent` 工具增加 `run_in_background` 参数，后台类型命名为 `local_agent`。
  Rationale: 用户确认要扩展 local agent。OneCode 现有 `tools/agent/tool.py` 是 subagent 唯一入口；新增参数比新增 `background_agent` 工具更贴近参考实现，也保留同步和后台两种调用形态。
  Date/Author: 2026-06-07 / Codex

- Decision: 后台完成不主动唤醒 agent；`local_bash` 和 `local_agent` 通知在下一次用户输入时 drain。
  Rationale: 当前 CLI 主循环只在用户输入后调用 `runtime.loop.stream()`，没有调度器能安全地在后台任务完成时自动发起新一轮模型调用。下一次用户输入时 drain 能复用现有 attachment collector，避免终端输入、权限 prompt 和模型调用并发竞争。
  Date/Author: 2026-06-07 / Codex

- Decision: `dream` 不向模型注入 `<task_notification>`。
  Rationale: Dream 是长期记忆维护任务，不是用户显式请求的后台操作。把它的完成通知注入对话会污染模型上下文并消耗 token。第一版只在 CLI 状态和 trace 中可见，成功或失败更新长期记忆 metadata。
  Date/Author: 2026-06-07 / Codex

- Decision: 用户下一次输入时如果 dream 仍在运行，本轮允许使用旧 memory。
  Rationale: 长期记忆 dream 是补漏和整理，不应阻塞用户继续工作。相关 memory context preparer 按当前磁盘内容读取即可；后台 dream 完成后，后续 turn 自然使用更新后的 memory。
  Date/Author: 2026-06-07 / Codex

- Decision: 后台 local_agent 第一版不弹交互式权限 prompt。
  Rationale: 参考实现可以依托 TUI/AppState 管理后台 agent 权限交互；OneCode CLI 的 prompter 使用终端 `input()`，后台任务中调用会和主输入循环竞争。第一版 background local_agent 复用 PermissionPolicy、guard、project settings 和 session grants；如果需要 ask 且没有预授权，则该工具调用返回 `permission_ask_required`，child 可能失败并生成后台任务失败状态。用户可以先用前台操作建立 session/project 授权，再启动后台 agent。
  Date/Author: 2026-06-07 / Codex

- Decision: 后台输出文件放在 `.onecode/<session_id>/background-tasks/<task_id>.output`。
  Rationale: 这是 session-local、workspace-local、易于检查的 runtime 管理目录，和 transcript、trace、tool-results 同一生命周期。并且不同 session 之间不会互相覆盖。`TaskOutputTool` 不实现，模型可用 `read_file` 读取通知中的 output file。
  Date/Author: 2026-06-07 / Codex

- Decision: 停止工具命名为 `background_task_stop`。
  Rationale: 当前已有 Todo task 工具 `task_create/task_get/task_update/task_list`。使用 `background_task_stop` 可以避免模型误以为它停止 Todo 工作项，也符合用户确认。
  Date/Author: 2026-06-07 / Codex

## Outcomes & Retrospective

本计划第一版已经实现。新增 `services/background_tasks/`，新增 `tools/background_task_stop/`，扩展 `tools/bash` 和 `tools/agent` 的 `run_in_background` 输入，CLI 新增 `/background-tasks` 并在 `/status` 显示后台任务摘要。后台 bash 使用 `Popen` 写入 `.onecode/<session_id>/background-tasks/<task_id>.output` 并由后台线程监控完成；后台 local agent 使用 `asyncio.Task` 复用 `SubagentRunner`，但禁用后台 child 的交互式 permission prompter；后台完成通知通过 `AttachmentCollector` 下一次用户输入 drain，并由 `AttachmentProjector` 投影为 `<task_notification>`。长期记忆提取拆分为 prepare/run job，`TURN_STOPPED` hook 现在启动不通知模型的 `d_...` dream task。

验证结果：定向测试 `uv run python -m pytest tests/test_background_task_manager.py tests/test_background_bash_tool.py tests/test_background_agent_tool.py tests/test_attachment_projector.py tests/test_cli_commands.py tests/test_long_term_memory_extraction.py tests/test_bash_tool.py tests/test_agent_tool.py tests/test_subagent_runner.py -q` 通过，结果为 `41 passed`。全量编译 `uv run python -m compileall core services infrastructure prompts tools ui` 通过。全量测试 `uv run python -m pytest tests -q` 通过，结果为 `300 passed`。

未完成范围保持原计划取舍：没有实现 remote agent、local workflow、monitor MCP、TaskOutputTool、后台完成主动唤醒 agent、跨进程任务恢复或 UI 面板。后台任务状态仍是当前 Python 进程内状态，输出文件持久留在 session 目录中。

## Context and Orientation

OneCode 是 Python code agent runtime。主循环在 `core/loop.py`，它只负责编排用户输入、模型调用、工具执行、消息写回和 transition。新增后台任务不能在 `core/loop.py` 里增加 `bash`、`agent` 或 `dream` 的工具名分支。后台任务必须通过服务层、工具 descriptor、attachment collector、hook 和 CLI 装配接入。

现有 Todo task system 位于 `services/tasks/` 和 `tools/task_create`、`tools/task_get`、`tools/task_update`、`tools/task_list`。它是文件持久化工作项追踪系统，不是异步执行系统。它把任务 JSON 写到 `.onecode/tasks/<task_list_id>/`，数字 ID 从 `1` 递增。后台任务执行系统不得修改这些语义。

现有工具体系位于 `services/tools/`。每个工具通过 `ToolDescriptor` 暴露 schema、prompt、validator、classifier 和 handler。`RegistryToolExecutor` 在 handler 前统一执行 schema validation、工具 validation、input-aware classification、guard、permission policy 和 hook；handler 返回 `ToolExecutionResult`。后台 bash 和后台 agent 必须仍然从这些 descriptor 入口启动，不能绕过 executor 的权限和 hook 边界。

现有 `bash` 工具在 `tools/bash/tool.py`，使用 `tools/bash/runner.py::GitBashRunner` 同步执行 Git Bash 命令，默认 `subprocess.run()` 并等待完成。后台 bash 不能复用 `subprocess.run()`，因为它会阻塞 handler；应新增基于 `subprocess.Popen` 的后台启动逻辑，stdout/stderr 直接写到 output file。

现有 `agent` 工具在 `tools/agent/tool.py`，调用 `services/subagents/runner.py::SubagentRunner.run()` 同步 drain child runtime 后返回最终摘要。后台 local agent 仍应复用 `SubagentRunner` 的 child runtime 装配，但由后台任务 manager 启动 `asyncio.Task`，让 tool handler 立即返回。

现有 attachment 系统位于 `services/attachments/`。`AttachmentCollector.collect_for_user_turn()` 在每次用户输入时收集用户 `@file`、shared runtime source 和 main-thread-only 文件变更。它已经定义 `QueuedAttachmentSource` 协议，允许 runtime source 在用户下一次输入时交出待注入 attachment。`AttachmentProjector` 会在模型调用前把 internal `role="attachment"` 投影成 provider-visible messages。后台任务通知应接入这条路径。

现有长期记忆自动提取在 `services/memory/extraction.py`。当前 `LongTermMemoryExtractionService.maybe_extract_after_model_response()` 在 `HookEvent.TURN_STOPPED` callback 中被 await，因此会阻塞 turn 自然结束。它用 `SubagentRequest(metadata={"purpose": "long_term_memory_extraction"})` 运行受限 fork child。后台 dream 的目标是复用这套提取逻辑，但让它作为后台任务运行并可见。

本计划使用的“后台任务”指“启动后不阻塞当前工具调用或当前 turn 的运行中工作”。后台任务状态只存在于当前 Python 进程内；第一版不要求进程退出后恢复仍在运行的任务。任务输出文件会留在磁盘，便于用户和模型读取，但任务状态本身不做跨进程持久恢复。

## Plan of Work

第一阶段新增后台任务 service。创建 `services/background_tasks/`，包含 `__init__.py`、`types.py`、`ids.py`、`output.py`、`manager.py` 和 `notifications.py`。`types.py` 定义 `BackgroundTaskType = Literal["local_bash", "local_agent", "dream"]`，`BackgroundTaskStatus = Literal["pending", "running", "completed", "failed", "killed"]`，以及 `BackgroundTaskState` dataclass。基础字段必须包括 `id`、`type`、`status`、`description`、`tool_use_id`、`start_time`、`end_time`、`output_file`、`notified`、`metadata`。`local_bash` state 额外保存 `command`、`process`、`exit_code`；`local_agent` state 额外保存 `prompt`、`agent_type`、`asyncio_task`、`child_session_id`、`final_text`；`dream` state 额外保存 `sessions_reviewing` 或 `memory_dir`、`asyncio_task`、`result_session_id`。

`ids.py` 提供 `generate_background_task_id(task_type)`。前缀固定为：`local_bash -> b_`，`local_agent -> a_`，`dream -> d_`。后缀用短随机十六进制或 URL-safe 字符串，不能使用数字 Todo ID。测试应断言三个前缀稳定。

`output.py` 管理输出路径和安全写入。定义 `background_task_output_dir(workspace, session_id)` 返回 `.onecode/<session_id>/background-tasks/`，`background_task_output_path(workspace, session_id, task_id)` 返回 `<task_id>.output`。创建输出文件时必须 `mkdir(parents=True, exist_ok=True)`。bash stdout/stderr 直接传给 `Popen` 的同一个文件句柄；agent 和 dream 可以把阶段摘要、child session id、final text 和 error 写入该文件。第一版不实现 5GB cap，但要设置合理测试路径并避免把大量输出读入内存。

`manager.py` 定义 `BackgroundTaskManager`。它持有 workspace、session state provider 或当前 `RuntimeState`、trace recorder、一个 `threading.RLock`、任务字典和通知队列。提供 `register_local_bash(...)`、`register_local_agent(...)`、`register_dream(...)`、`get(task_id)`、`list_tasks()`、`stop(task_id)` 和 `drain_notifications(state)`。所有 task dict 修改必须持锁。终端任务保留在内存中，直到被通知 drain 或超过简单保留窗口；第一版可以不做自动 eviction，但 `/background-tasks` 应显示终端状态。

`notifications.py` 定义后台通知 payload 和 `BackgroundTaskNotificationSource`。该 source 实现 `QueuedAttachmentSource.collect(state)`，调用 manager 的 drain 方法，并返回 attachment payload。新增 attachment type 推荐为 `background_task_notification`，字段包括 `task_id`、`task_type`、`status`、`summary`、`output_file`、可选 `tool_use_id`。编辑 `services/attachments/projector.py`，把该 attachment 投影成模型可见 XML：

    <task_notification>
    <task_id>b_123</task_id>
    <task_type>local_bash</task_type>
    <output_file>.onecode/.../b_123.output</output_file>
    <status>completed</status>
    <summary>Background command "npm test" completed (exit code 0)</summary>
    </task_notification>

`dream` completion 不进入该通知队列。只有 `local_bash` 和 `local_agent` 会设置 `notified=False` 并等待下一次用户输入 drain。

第二阶段扩展 CLI 装配。编辑 `ui/cli/types.py::CliRuntime`，增加 `background_task_manager: BackgroundTaskManager | None = None`。编辑 `ui/cli/app.py::build_runtime()`，在创建 hooks 和 task store 附近创建 `background_task_manager = BackgroundTaskManager(workspace=workspace, trace_recorder=trace_recorder)`。创建 `AttachmentCollector` 时传入 `shared_sources=(BackgroundTaskNotificationSource(background_task_manager),)`，并确保 `CliRuntime.with_session()` 复用同一个 manager 但能让 manager 使用新的 `state.session_id` 生成新 output dir。推荐 manager 不缓存 session id，而是每次启动任务时从传入的 `RuntimeState` 读取当前 session。

编辑 `ui/cli/renderer.py`，让 `render_status()` 显示后台任务摘要，例如 running/completed/failed/killed 数量和最近几个任务。新增 `render_background_tasks(runtime, tasks)`，显示 ID、type、status、description、output file 和 exit/result 简短信息。编辑 `ui/cli/commands.py`，新增 `/background-tasks` 命令，并在 `render_banner()` 和 `render_help()` 中加入该命令。这个命令只读内存任务状态，不修改任务。

第三阶段扩展 `bash` 工具。编辑 `tools/bash/tool.py::BashInput`，新增 `run_in_background: bool = False`。更新 `tools/bash/prompt.py`，说明慢命令可以设置 `run_in_background=true`，后台命令会立即返回任务 ID，完整输出写入 output file，完成通知会在用户下一次输入时出现。编辑 descriptor factory，使 `descriptor(background_task_manager: BackgroundTaskManager | None = None)` 可接收 manager；测试或最小装配仍可调用 `descriptor()`。如果模型请求 `run_in_background=true` 但没有 manager，handler 返回 `is_error=True`，说明后台任务未启用。

`bash` 的 classification 应继续基于原命令做 AST、readonly、target 和 permission 判断。后台执行不能绕过 preflight。对 `run_in_background=true` 的 handler，权限通过后调用 manager 启动 Popen，而不是调用 `GitBashRunner.run()`。后台 Popen 使用 `find_git_bash()` 找到 bash，命令参数保持 `[bash, "--noprofile", "--norc", "-lc", command]`，cwd 使用 `runtime.guard.boundary.cwd` 或当前工作目录。stdout 和 stderr 都写到同一 output file。handler 立即返回 `ToolExecutionResult`，content 至少包含 task id、command、status running 和 output file。

后台 bash 的 timeout 语义要显式区别于前台。前台 bash 保留默认 `DEFAULT_TIMEOUT_MS=120_000`。后台 bash 只有在用户显式传入 `timeout_ms` 时才设置 watchdog；没有显式 timeout 时不自动 120 秒杀掉，因为后台路径主要服务 `npm install`、build、test 等慢操作。watchdog 可以用 `asyncio` 或 thread timer 等待进程并在超时后 kill，最终状态记为 failed，metadata 标记 `timed_out=True`。

第四阶段扩展 `agent` 工具。编辑 `tools/agent/tool.py::INPUT_SCHEMA`，新增 `run_in_background` boolean。更新 `tools/agent/prompt.py`，移除“Do not pass run_in_background”规则，改为说明同步和后台模式：普通调用等待 child summary；`run_in_background=true` 启动 `local_agent` 后立即返回任务 ID。descriptor factory 改为 `descriptor(runner, background_task_manager: BackgroundTaskManager | None = None)`。同步路径保持现有行为；后台路径创建 `SubagentRequest`，metadata 包含 `background_task_id`、`background_task_type="local_agent"`、父 session、tool call id、task list id 继承字段，并交给 manager 启动。

后台 local agent 复用 `SubagentRunner.run()`，但必须避免后台 interactive permission prompt。编辑 `services/subagents/runner.py`，增加判断：当 `request.metadata.get("background_task_id")` 存在且 purpose 不是长期记忆内部 dream 时，child `RegistryToolExecutor` 使用 `permission_prompter=None`。这会让需要 ask 的工具调用返回结构化 tool error，而不是抢占 CLI 输入。已被 project settings 或 session grants allow 的工具仍可运行；read-only 和 guard allow 的工具仍可运行。测试应覆盖后台 local_agent 在需要 ask 时不会调用 fake prompter。

第五阶段新增 `background_task_stop` 工具。创建 `tools/background_task_stop/__init__.py`、`tool.py` 和 `prompt.py`。输入 schema 包含必填 `task_id`。handler 调用 `BackgroundTaskManager.stop(task_id)`。如果任务不存在，返回 `is_error=True`，error 为 `background_task_not_found`。如果任务已经终端，返回普通结果说明当前状态。对 running bash，stop 应先尝试 `process.terminate()`，短暂等待后必要时 `process.kill()`，状态置为 `killed`，写入输出文件一行 `[background task stopped]`。对 running local_agent 或 dream，stop 应 cancel 对应 `asyncio.Task`，状态置为 `killed`，并让最终清理逻辑不要再覆盖成 failed。把 descriptor 注册到 `ui/cli/app.py` 的 base descriptors。

第六阶段将长期记忆提取改为后台 dream。重构 `services/memory/extraction.py`，把“判断是否应该提取”和“实际运行受限 fork child”拆开。推荐新增 dataclass `LongTermMemoryExtractionJob`，包含 messages、assistant_message、tool_calls、usage、parent_session_id、parent_tool_call_id、memory_dir 和 max_turns。新增方法 `prepare_extraction_job(...) -> LongTermMemoryExtractionJob | str`，返回 job 或 skip reason；新增 `run_extraction_job(job, state) -> None`，内部复用现有 `_extraction_prompt()`、`SubagentRequest(metadata={"purpose": "long_term_memory_extraction", ...})`、cursor 推进和 metadata 更新逻辑。

编辑 `ui/cli/app.py` 中注册 `HookEvent.TURN_STOPPED` 的 callback。当前 callback 直接 await `long_term_memory_extractor.maybe_extract_after_model_response(...)`。改为：调用 extractor 的 prepare 方法；如果 skip，更新 metadata 和 trace 后返回；如果需要 extract，调用 `background_task_manager.register_dream(...)` 启动后台任务并立刻返回。dream 任务内部 await `long_term_memory_extractor.run_extraction_job(...)`。dream 启动时 `state.metadata["long_term_memory_extraction"]["running"] = True`，完成后由 extraction service 写 success/failed 和 running false。dream 不 enqueque model notification。

必须保留长期记忆安全边界。Dream child 仍使用 `request.metadata["purpose"] == "long_term_memory_extraction"`，仍设置 `long_term_memory_extraction_agent=True` 和 `allowed_memory_dir`，仍由 `PermissionPolicy._long_term_memory_extraction_decision()` 限制读写。不要为了后台化降低权限。

第七阶段更新 prompt、status 和 tests。`bash` 和 `agent` prompt 必须让模型知道后台任务完成通知只会在用户下一次输入时出现，不会自动唤醒。`background_task_stop` prompt 必须说明它只停止后台执行任务，不停止 Todo task。`/status` 中长期记忆 extraction 行应能显示 `running=True`，同时 background task 摘要里能看到 `d_... dream running`。

测试应新增 `tests/test_background_task_manager.py`，覆盖 ID 前缀、output path、register/list、notification drain、stop missing、stop terminal。新增 `tests/test_background_bash_tool.py`，用 fake Popen 或很短命令覆盖后台启动立即返回、输出文件路径、completion 状态和 notification。新增 `tests/test_background_agent_tool.py`，用 fake `SubagentRunner` 覆盖 `run_in_background` 立即返回、最终状态、通知 drain、permission prompter 不被调用。新增或扩展 `tests/test_long_term_memory_extraction.py`，覆盖 `TurnStopped` 启动 dream 后不阻塞、dream 不产生 model notification、dream running 时下一轮仍允许旧 memory。新增 `tests/test_cli_commands.py` 覆盖 `/background-tasks` 和 `/help`。

## Concrete Steps

从仓库根目录运行所有命令：

    cd D:\study\OneCode

开始实现前确认工作树状态，避免覆盖他人变更：

    git status --short

预期当前可以为空。如果有未提交变更，先判断是否与后台任务相关；不要还原用户或其他进程的无关变更。

实现服务骨架后先运行最小导入和定向测试：

    uv run python -m pytest tests/test_background_task_manager.py -q
    uv run python -m compileall services/background_tasks

扩展 bash 后运行：

    uv run python -m pytest tests/test_background_bash_tool.py tests/test_bash_tool.py -q
    uv run python -m compileall tools/bash services/background_tasks

扩展 agent 后运行：

    uv run python -m pytest tests/test_background_agent_tool.py tests/test_agent_tool.py tests/test_subagent_runner.py -q
    uv run python -m compileall tools/agent services/subagents services/background_tasks

加入 dream 后运行：

    uv run python -m pytest tests/test_long_term_memory_extraction.py tests/test_permission_policy.py -q
    uv run python -m compileall services/memory services/subagents services/background_tasks ui

最后运行：

    uv run python -m compileall core services infrastructure prompts tools ui
    uv run python -m pytest tests -q

手动 CLI 验证需要 `.env` 中已有可用 provider 设置。启动：

    uv run python -m ui.cli.app

在 CLI 中输入：

    /tools

预期看到 `bash` schema/prompt 支持 `run_in_background`，`agent` schema/prompt 支持 `run_in_background`，并看到 `background_task_stop`。

让模型启动一个短后台 bash：

    Run `python -c "import time; print('start'); time.sleep(1); print('done')"` in the background.

预期工具结果立即包含 `b_...` 和 `.onecode/<session_id>/background-tasks/b_....output`。等待两秒后输入：

    Check whether the background command finished.

预期本轮上下文包含 `<task_notification>`，模型能说明命令完成并可读 output file。输入：

    /background-tasks

预期看到该任务状态为 `completed`，并显示 output file。

验证 stop：

    Run `python -c "import time; time.sleep(60)"` in the background.

拿到 task id 后让模型调用 `background_task_stop`，或在测试中直接调用工具。预期状态变为 `killed`，输出文件包含 stopped 标记。

验证 dream：制造一个会触发长期记忆自动提取的普通对话自然停止。预期当前 turn 不等待提取完成；`/status` 显示 long-term memory extraction running 或最近状态；`/background-tasks` 显示 `d_... dream`。dream 完成后不会在下一次用户输入时出现 `<task_notification>`，但 `.onecode/memory/` 和 `/status` 反映最终结果。

## Validation and Acceptance

后台任务服务验收要求：`BackgroundTaskManager` 可以注册、列出、停止和终结任务；任务 ID 前缀稳定；状态只能在 `pending/running/completed/failed/killed` 中；所有输出文件在 `.onecode/<session_id>/background-tasks/`；并发状态更新不会抛出异常或丢失任务；终端任务保留足够时间供 `/background-tasks` 查看。

后台 bash 验收要求：`bash(run_in_background=true)` 通过和前台 bash 相同的 AST、guard 和 permission preflight；权限通过后立即返回，不等待进程结束；stdout/stderr 写入同一个 output file；进程 exit code 0 变为 completed，非 0 变为 failed；显式 timeout 会杀掉进程并标记 failed/timed_out；`background_task_stop` 能杀掉运行中的 bash 并标记 killed。

后台 local agent 验收要求：`agent(run_in_background=true)` 启动 `local_agent` task 并立即返回；后台 child 使用现有 `SubagentRunner`、tool registry、guard 和 permission policy；不弹交互式 CLI permission prompt；预授权的工具可以运行，需要 ask 且未授权的工具返回 tool error 并使 child 按现有 subagent 错误路径结束；完成或失败后生成下一次用户输入可 drain 的 `<task_notification>`。

通知验收要求：后台 bash 和 local_agent 完成后不会主动调用模型；下一次用户输入时 `AttachmentCollector.collect_for_user_turn()` 从 `BackgroundTaskNotificationSource` drain 通知；`AttachmentProjector` 把通知投影为 `<task_notification>` XML；同一个任务只通知一次；dream 任务不进入通知队列。

Dream 验收要求：自然停止后，长期记忆提取从阻塞变为后台 dream task；dream 仍使用 `purpose="long_term_memory_extraction"` 和受限权限；dream running 时下一次用户输入可以继续，使用当前磁盘上的旧 memory；dream 成功后 cursor 推进、metadata 记录 success、memory 文件可能更新；dream 失败只记录状态和 trace，不破坏主会话；dream stop 会 cancel child task 并记录 killed。

CLI 验收要求：`/help` 和 banner 包含 `/background-tasks`；`/background-tasks` 能显示空状态、running、completed、failed、killed；`/status` 显示后台任务摘要和长期记忆 dream 状态。CLI 不实现后台任务面板，不自动唤醒 agent。

安全验收要求：`core/loop.py` 不出现后台任务类型或工具名分支；`services/tasks/` Todo system 不被改造成后台系统；后台 bash 和 local_agent 不绕过 executor preflight；permission deny、guard deny、project settings deny 和 read-only agent deny 仍生效；dream 权限仍只允许长期记忆提取 agent 的既有读写范围。

测试验收要求：新增测试在实现前应失败，完成后通过。最终 `uv run python -m pytest tests -q` 应通过；如果存在无关失败，必须在本计划 `Surprises & Discoveries` 和 `Outcomes & Retrospective` 记录命令、失败名称和判断依据。

## Idempotence and Recovery

重复运行 `/background-tasks` 和 `/status` 不修改磁盘。重复 drain 通知不会重复注入同一任务通知；实现应在 manager 中标记 `notified=True` 或从通知队列移除事件。输出目录创建必须幂等，不清空已有 output file。

如果后台 bash 进程已经退出，再调用 stop 应返回当前终端状态，不应抛异常。若 stop 和进程自然退出竞争，最终状态只允许是 `killed` 或自然终端状态之一，不能出现重复通知或状态回退。实现时在写终端状态前重新检查当前状态，如果已经 terminal，不要覆盖。

如果 CLI `/clear` 开启新 session，旧 session 的已运行后台任务可以继续存在于 manager 中，但新启动任务应写到新 session 的 output dir。第一版不要求跨 `/clear` 自动把旧任务输出注入新 session；`/background-tasks` 可以显示所有内存任务并包含 output file。若实现者选择在 `/clear` 时 kill running tasks，必须记录新的 Decision，并更新验收。

如果进程退出，内存任务状态丢失是第一版可接受行为。输出文件已经在 `.onecode/<session_id>/background-tasks/`，用户可手动读取。不要尝试从 output 文件恢复 Popen 或 asyncio task。

如果 dream 被 kill 或失败，不要推进长期记忆 cursor，下一次满足条件时可以重试。若 dream 仍在运行又触发新的 turn stopped，第一版应记录 `skipped_running` 或保留现有 running 状态，不启动第二个 dream。

## Artifacts and Notes

参考实现的 local_bash 行为摘要：`LocalShellTask` 注册 `local_bash` state，`shellCommand.background(taskId)` 让进程继续运行，stdout/stderr 由 `TaskOutput` 写入文件；完成时 enqueue `<task_notification>`，包含 task id、output file、status 和 summary；kill 通过 `shellCommand.kill()` 并把状态置为 killed。

参考实现的 local_agent 行为摘要：`LocalAgentTask` 注册 `local_agent` state，保存 prompt、agent type、AbortController、progress、pending messages 和 output file；输出文件通常是 child transcript 的 symlink；completion notification 由 AgentTool 路径发送；kill 调用 abort controller。LocalAgentTask 本身不定义独立权限策略，权限仍在 agent 工具执行上下文中处理。

OneCode 第一版对 local_agent 的差异：由于 CLI 权限 prompter 基于 `input()`，后台 child 不弹交互式权限 prompt。它使用已有 project/session 授权和 read-only/guard allow；未授权 ask 会作为工具错误进入 child transcript。这个取舍是为了避免后台任务和主 CLI 输入循环争用终端。

推荐模型可见后台 bash 启动结果：

    Background task started.
    task_id: b_ab12cd34
    task_type: local_bash
    status: running
    command: npm test
    output_file: .onecode/6f.../background-tasks/b_ab12cd34.output

推荐后台任务通知：

    <task_notification>
    <task_id>b_ab12cd34</task_id>
    <task_type>local_bash</task_type>
    <output_file>.onecode/6f.../background-tasks/b_ab12cd34.output</output_file>
    <status>completed</status>
    <summary>Background command "npm test" completed (exit code 0)</summary>
    </task_notification>

推荐 `/background-tasks` 输出：

    Background tasks:
      b_ab12cd34 [local_bash completed] npm test
        output: .onecode/6f.../background-tasks/b_ab12cd34.output
      d_f00ba421 [dream running] updating long-term memory
        output: .onecode/6f.../background-tasks/d_f00ba421.output

## Interfaces and Dependencies

在 `services/background_tasks/types.py` 中定义：

    BackgroundTaskType = Literal["local_bash", "local_agent", "dream"]
    BackgroundTaskStatus = Literal["pending", "running", "completed", "failed", "killed"]

    @dataclass
    class BackgroundTaskState:
        id: str
        type: BackgroundTaskType
        status: BackgroundTaskStatus
        description: str
        output_file: str
        start_time: float
        end_time: float | None = None
        tool_use_id: str | None = None
        notified: bool = False
        metadata: dict[str, Any] = field(default_factory=dict)

可以根据 Python 类型安全需要定义 `LocalBashTaskState`、`LocalAgentTaskState` 和 `DreamTaskState` 子类，或者把 type-specific 字段放入 metadata；但 public manager API 必须返回足够信息供 tests 和 CLI 渲染。

在 `services/background_tasks/ids.py` 中定义：

    def generate_background_task_id(task_type: BackgroundTaskType) -> str: ...

在 `services/background_tasks/output.py` 中定义：

    def background_task_output_dir(workspace: Path | str, session_id: str) -> Path: ...
    def background_task_output_path(workspace: Path | str, session_id: str, task_id: str) -> Path: ...

在 `services/background_tasks/manager.py` 中定义：

    class BackgroundTaskManager:
        def list_tasks(self) -> tuple[BackgroundTaskState, ...]: ...
        def get(self, task_id: str) -> BackgroundTaskState | None: ...
        def start_bash(self, *, command: str, description: str, state: RuntimeState, cwd: Path, tool_use_id: str | None, timeout_ms: int | None = None) -> BackgroundTaskState: ...
        def start_agent(self, *, request: SubagentRequest, runner: SubagentRunner, description: str, state: RuntimeState, tool_use_id: str | None) -> BackgroundTaskState: ...
        def start_dream(self, *, job: LongTermMemoryExtractionJob, extractor: LongTermMemoryExtractionService, state: RuntimeState) -> BackgroundTaskState: ...
        def stop(self, task_id: str) -> BackgroundTaskState | None: ...
        def drain_notifications(self, state: RuntimeState) -> tuple[dict[str, Any], ...]: ...

如果 circular import 出现，使用 Protocol 类型放在 `services/background_tasks/types.py`，避免 `services/background_tasks` 静态依赖 `ui.cli` 或具体 tool modules。

在 `services/background_tasks/notifications.py` 中定义：

    class BackgroundTaskNotificationSource:
        def __init__(self, manager: BackgroundTaskManager) -> None: ...
        def collect(self, state: RuntimeState) -> tuple[dict[str, Any], ...]: ...

返回 payload 的 `type` 为 `background_task_notification`。

在 `tools/bash/tool.py` 中调整：

    class BashInput(BaseModel):
        command: str
        timeout_ms: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT_MS)
        description: str | None = None
        run_in_background: bool = False

    def descriptor(background_task_manager: BackgroundTaskManager | None = None) -> ToolDescriptor: ...

在 `tools/agent/tool.py` 中调整：

    INPUT_SCHEMA["properties"]["run_in_background"] = {"type": "boolean"}

    def descriptor(
        runner: SubagentRunner,
        background_task_manager: BackgroundTaskManager | None = None,
    ) -> ToolDescriptor: ...

在 `tools/background_task_stop/tool.py` 中定义：

    def descriptor(background_task_manager: BackgroundTaskManager) -> ToolDescriptor: ...

在 `services/memory/extraction.py` 中定义可后台调用的 job 边界：

    @dataclass(frozen=True)
    class LongTermMemoryExtractionJob:
        messages: tuple[dict[str, Any], ...]
        parent_session_id: str
        parent_tool_call_id: str
        allowed_memory_dir: str
        max_turns: int

    class LongTermMemoryExtractionService:
        def prepare_extraction_job(... ) -> LongTermMemoryExtractionJob | None: ...
        async def run_extraction_job(self, job: LongTermMemoryExtractionJob, state: RuntimeState) -> None: ...

方法名可以不同，但必须把“准备/跳过决策”和“执行 fork child”分开，让 hook callback 能快速启动 dream 后返回。

在 `ui/cli/types.py::CliRuntime` 中新增：

    background_task_manager: BackgroundTaskManager | None = None

在 `ui/cli/renderer.py` 中新增：

    def render_background_tasks(runtime: CliRuntime, tasks: Iterable[BackgroundTaskState]) -> str: ...

在 `ui/cli/commands.py` 中新增 `/background-tasks` 分支。

## Revision Notes

- 2026-06-07 / Codex: 初始版本。根据用户确认的范围撰写完整中文 ExecPlan，选择扩展 `bash` 和 `agent` 的 `run_in_background`，新增独立 `services/background_tasks`，通知通过下一次用户输入的 queued attachment drain，停止工具命名为 `background_task_stop`，输出文件放入 session-local `.onecode/<session_id>/background-tasks/`，并将长期记忆自动提取后台化为不通知模型的 `dream` task。
- 2026-06-07 / Codex: 实施第一版主体代码，记录工具 descriptor、attachment source 和 hook 足以承载后台任务而不需要修改主循环；补充自动化测试、compileall 和全量 pytest 结果，并明确仍不实现主动唤醒、TaskOutputTool 和跨进程恢复。
