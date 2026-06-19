# 重构 CLI 运行中输入框与命令排队机制

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文档遵循仓库根目录 `PLANS.md`。后续实现者必须按 `PLANS.md` 维护本文：每次推进实现、发现事实、改变决策或完成验收，都要同步更新本文，并保持本文自包含。

## Purpose / Big Picture

完成此变更后，用户在 OneCode CLI 的 agent 正在输出、思考或执行工具时，仍然能在底部输入框继续输入下一条命令。按 Enter 后，输入框立即清空，命令进入可见队列预览；当前 agent turn 完成后，CLI 会按队列顺序自动执行这些输入。用户可以通过启动 `uv run python -m ui.cli.app`，提交一个会触发较长输出或工具调用的请求，然后在输出过程中继续输入第二条请求来观察效果：第二条请求不会打断当前 turn，也不会丢失，而是在当前 turn 结束后自动执行。

本计划是替换式重构。当前代码里已经存在 `ui/cli/terminal/queue.py::InputQueue` 和 `ui/cli/terminal/prompt_session.py::PromptSession.read(queue_mode=True)`，但 `InlineRepl._run_turn()` 运行期间不会再调用 `PromptSession.read()`，所以这条旧路径不能实现用户想要的“输出时仍能输入”。本计划将运行中输入合并进 `StreamingSession` 动态区，迁移完成后删除旧的 `PromptSession.queue_mode` 兼容分支和只覆盖旧路径的测试。

## Progress

- [x] (2026-06-18 00:00+08:00) 已阅读 `PLANS.md`，确认 ExecPlan 必须自包含、可执行、持续维护，并且写入 `.md` 文件时不需要外层 fenced code block。
- [x] (2026-06-18 00:00+08:00) 已阅读项目架构文档、CLI 架构文档、当前 CLI streaming 相关代码和用户提供的参考机制，确认该功能属于 `ui/cli/terminal` 交互层，不应进入 `core/loop.py` 主循环。
- [x] (2026-06-18 00:00+08:00) 已确认当前 `InputQueue` 只是 FIFO 字符串队列，`PromptSession.queue_mode` 在真实运行中不可达，因为 `_run_turn()` 会独占等待 `StreamingSession.run()` 完成。
- [x] (2026-06-18 00:00+08:00) 已创建本 ExecPlan 初稿。
- [x] (2026-06-18 00:00+08:00) Milestone 1：把 `InputQueue` 升级为正式的 queued input 模型（`QueuedInput` dataclass，字段：text、kind、sequence、visible），并补充队列本身的测试（分类、空白拒绝、单调 sequence、snapshot 只读、clear）。
- [x] (2026-06-18 00:00+08:00) Milestone 2：让 `StreamingSession` 在运行中动态区内显示可编辑输入框（buffer + `accept_handler` push 到 `InputQueue`，复用 `InlineCompleter`），并把 Enter 提交变成入队。Esc/Ctrl-C 仍然只取消当前 turn，不退输入。
- [x] (2026-06-18 00:00+08:00) Milestone 3：在动态区渲染 queued preview（`render_queued_inputs` + `render_stream_body_ansi` 新增 `queued_inputs` 形参），最多 3 条可见 + overflow 摘要 + 文本截断，仅在动态区显示。
- [x] (2026-06-18 00:00+08:00) Milestone 4：让 `InlineRepl` 在当前 turn 结束后 drain 队列（`_drain_queue`），按 FIFO 顺序执行：普通 prompt → `_run_turn`，slash command → `_handle_command`；`/exit` 类命令清空 runtime 时 drain 立即中止。
- [x] (2026-06-18 00:00+08:00) Milestone 5：删除旧 `PromptSession.queue_mode` 兼容路径（`read()` 形参、`SubmissionKind.QUEUE`、`_on_enter` 入队分支、`print_static` 旧通知）、删除 `test_enter_in_queue_mode_pushes_to_queue`、更新 `docs/design-docs/cli-architecture.md` 和 `docs/design-docs/cli-message-rendering-architecture.md`。
- [x] (2026-06-18 00:00+08:00) Milestone 6：聚焦测试 + 编译检查通过；`queue_mode` / `SubmissionKind.QUEUE` 在 `ui` 和 `tests` 中无残留；预存在的 `test_bash_tool` / `test_search_tools` 失败与本计划无关。

## Surprises & Discoveries

- Observation: OneCode 当前已经有 `InputQueue`，但它只保存字符串，不能表达输入类型、显示策略或未来优先级。
  Evidence: `ui/cli/terminal/queue.py::InputQueue` 目前包装 `collections.deque[str]`，`push()` 只做 `rstrip()` 后 append。

- Observation: `PromptSession` 的 `queue_mode=True` 是半成品路径，真实运行中不会在 agent 输出期间被调用。
  Evidence: `ui/cli/terminal/repl.py::_main_loop()` 在普通提交后执行 `await self._run_turn(text)`；`_run_turn()` 内部等待 `StreamingSession.run(events)` 完成，期间主循环不会再次调用 `self._prompt.read(queue_mode=True)`。

- Observation: 参考实现依赖 React 外部 store 和 `useSyncExternalStore` 触发重新渲染；OneCode CLI 使用 prompt_toolkit 的单个动态区应用，所以不能逐字照搬 React hook 结构。
  Evidence: 参考文件 `docs/references/s04_hooks/useQueueProcessor.ts` 通过 `useSyncExternalStore` 监听 query guard 和 queue；OneCode 的 `ui/cli/terminal/stream_session.py` 用 `prompt_toolkit.Application(full_screen=False, erase_when_done=True)` 驱动运行中动态区。

- Observation: 当前工作树已有 checkpoint streaming 重构相关文件，例如 `ui/cli/terminal/stream_state.py`、`stream_reducer.py`、`stream_view.py` 和 `output_coordinator.py`。运行中输入改造必须尊重“静态区写入只走 coordinator，动态区只由 prompt_toolkit 应用绘制”的边界。
  Evidence: `ui/cli/terminal/stream_session.py` 的模块说明已经把 reducer、view、coordinator 分层写清楚；`TerminalOutputCoordinator` 是流式会话里唯一允许写静态区的组件。

## Decision Log

- Decision: 运行中输入框和命令排队属于 `ui/cli/terminal`，不改 `core/loop.py` 的主循环。
  Rationale: `AgentLoop` 的职责是上下文重建、模型调用、工具执行和 transition 编排。排队是用户输入调度和终端显示问题，放进 core 会让 runtime 依赖 CLI 交互语义。
  Date/Author: 2026-06-18 / Codex。

- Decision: 采用替换式重构，迁移完成后删除 `PromptSession.read(queue_mode=...)` 和 `SubmissionKind.QUEUE` 旧路径，不保留兼容分支。
  Rationale: 旧路径在真实运行中不可达，保留会制造两个输入模型，增加后续维护成本。新路径应该让 idle 输入归 `PromptSession`，running 输入归 `StreamingSession`。
  Date/Author: 2026-06-18 / Codex。

- Decision: 第一版只实现用户 prompt 和 slash command 的 `next` 队列，不实现 `now` 优先级和工具级 interrupt 行为。
  Rationale: 参考实现的 `now` 和 interrupt 依赖工具 `interruptBehavior`、AbortController 和 query guard 的完整取消模型。OneCode 当前工具执行取消语义尚未作为统一 public contract 暴露；第一版先保证不丢输入、按顺序执行，再单独设计中断。
  Date/Author: 2026-06-18 / Codex。

- Decision: 队列预览属于运行中动态区，不能通过静态区 `print_static()` 打印。
  Rationale: 静态区是 scrollback 中已经定稿的历史，动态区是可擦除的 live UI。运行中队列预览会频繁变化，应由 `stream_view` 渲染，否则会和 checkpoint 提交的 assistant/tool 输出竞争。
  Date/Author: 2026-06-18 / Codex。

## Outcomes & Retrospective

实现完成。新增 / 删除 / 验证清单如下：

### 新增能力

- **运行中输入框**：`StreamingSession` 在运行中动态区底部追加 prompt_toolkit `BufferControl`，复用 `InlineCompleter` 做 `/`、`@` 补全。Enter 通过 buffer 的 `accept_handler` 把文本 push 到共享 `InputQueue` 并 reset；Esc / Ctrl-C 仍只取消当前 turn。
- **`InputQueue` 升级为 typed queue**：携带 `QueuedInput(text, kind, sequence, visible)`，`kind` 区分 `prompt` / `slash`，单调 `sequence` 标识插入顺序，`snapshot()` 是只读快照，`pop()` 返回 `QueuedInput` 对象。空白输入不入队。
- **queued preview 渲染**：`render_stream_body_ansi` 接受新的 `queued_inputs` 形参，调用 `render_queued_inputs` 生成最多 3 条可见 + overflow 摘要 + 文本截断（默认 60 字符）。仅动态区显示，不写静态 scrollback，不绕过 coordinator。
- **`InlineRepl._drain_queue`**：当前 turn 结束后按 FIFO 弹出，`kind == "slash"` 走 `_handle_command`（不进 agent），`kind == "prompt"` 走 `_run_turn`。Slash 命令若把 `runtime` 替换为 `None`（如 `/exit`），drain 立即中止。
- **`InlineRepl._run_turn`** 现在把同一个 `self._queue` 和 `self._runtime` 传给 `StreamingSession`，这样底层的 input box 直接拿到补全和共享队列。

### 删除的旧路径

- `PromptSession.read(queue_mode=...)` 形参。
- `SubmissionKind.QUEUE` 枚举值。
- `_build_application` / `_build_key_bindings` 里的 `queue_mode` 分支和 `Enter → self._queue.push` 旧路径。
- `print_static("… queued: …")` 静态区通知（动态区现在自带 queued preview，不需要静态通知）。
- `tests/test_cli_terminal.py::test_enter_in_queue_mode_pushes_to_queue`（覆盖已删除的 `queue_mode=True` 路径）。
- `repl.py` 中不再需要的 `print_static` / `print_assistant_markdown` / `print_assistant_start` / `Text` 导入。

### 测试证据

- `tests/test_cli_terminal.py`：
  - 队列模型：`test_queue_is_fifo`、`test_queue_skips_blank_lines`、`test_queue_snapshot_is_readonly_copy`、`test_queue_classifies_slash_vs_prompt`、`test_queue_assigns_monotonic_sequence`、`test_queue_snapshot_returns_typed_records`、`test_queue_clear_drops_everything`。
  - 运行中输入框：`test_streaming_session_with_queue_enqueues_input_and_does_not_exit`、`test_streaming_session_running_input_buffer_clears_after_enter`、`test_streaming_session_running_input_classifies_slash_command`、`test_streaming_session_running_input_rejects_blank_submit`。
  - queued preview：`test_view_renders_queued_preview_with_limit`、`test_view_renders_queued_preview_truncates_long_text`、`test_view_queued_preview_empty_when_no_inputs`、`test_view_body_includes_queued_preview_when_passed`。
  - drain：`test_repl_drains_queue_in_fifo_order`、`test_repl_drain_stops_on_runtime_exit`、`test_repl_run_turn_passes_queue_into_streaming_session`。
- 既有 streaming 测试无回归：`test_streaming_session_drains_and_commits`、`test_streaming_session_commits_on_assistant_message_completed`、`test_streaming_session_cancels_on_escape`、`test_streaming_session_cancels_on_ctrl_c` 均通过。
- `tests/test_import_boundaries.py` 通过，确认 `core/loop.py` 未被改、新增逻辑留在 `ui/cli/terminal`。

### 自动化与边界

- `uv run python -m compileall ui core services` 通过。
- `rg -n "queue_mode|SubmissionKind\.QUEUE" ui tests --type py` 无结果。
- 全量 `uv run python -m pytest tests/`：555 通过，2 失败（`test_bash_tool.test_bash_descriptor_schema_and_prompt` 与 `test_search_tools.test_registry_generates_search_tool_schemas_and_prompts`），均与本计划无关（`git stash` 后 main 分支同样失败）。

### 后续可能另开计划的方向

- `now` 优先级 / 工具级 interrupt 行为：需要先在 `services/tools/executor.py` 暴露工具执行的统一取消契约，参考实现里的 `interruptBehavior` / AbortController 不直接套用。
- 后台任务通知：当 `BackgroundTaskManager` 完成时把通知 push 到运行中动态区的顶部，跨 turn 持久化。
- 排队中 slash 命令（`/clear`、`/resume`、`/connect`）目前能正确替换 runtime 并继续 drain，但需要专门的回归测试覆盖「`/clear` 后队列里仍有未执行的 prompt」这种场景。

## Context and Orientation

OneCode 是一个 Python code agent runtime。`core/loop.py::AgentLoop` 是运行时主循环，它通过 `AgentEvent` 流把 assistant 文本、工具状态和完成事件交给调用方。CLI 位于 `ui/cli/`，是当前用户交互界面。TTY 路径使用 prompt_toolkit 和 Rich 组合出“静态区 + 动态区”的终端界面。

本文使用几个术语。静态区指普通终端 scrollback，内容一旦打印就可向上滚动查看，当前由 `ui/cli/terminal/static_output.py` 和 `TerminalOutputCoordinator` 写入。动态区指 prompt_toolkit 的非全屏 `Application`，它会在结束时擦除，用于输入框、流式 assistant preview、工具状态和后续 queued preview。turn 指用户提交一次输入后，agent 从接收 prompt 到完成最终回答的完整过程，内部可能包含多次模型调用和工具调用。queued input 指用户在当前 turn 尚未结束时输入并提交、等待下一次执行的命令。

当前相关文件如下。`ui/cli/terminal/repl.py::InlineRepl` 是交互式 CLI 主循环，空闲时调用 `PromptSession.read()` 读取用户输入，提交后调用 `_run_turn()` 启动 agent。`ui/cli/terminal/prompt_session.py::PromptSession` 负责空闲态输入框、补全菜单和 Enter/Tab 语义。`ui/cli/terminal/stream_session.py::StreamingSession` 负责运行中动态区，消费 `AgentEvent` 并调用 reducer、view 和 coordinator。`ui/cli/terminal/stream_view.py` 把流式状态渲染成 prompt_toolkit 可显示内容。`ui/cli/terminal/queue.py::InputQueue` 是现有队列，但目前只保存字符串。`tests/test_cli_terminal.py` 已覆盖 prompt 输入、队列基础行为和 streaming session 行为，后续应扩展为新运行中输入路径的测试。

参考实现来自 `docs/references/`。参考实现是 React/Ink 架构，不能逐字搬运，但可学习它的职责拆分：同步 guard 判断是否正在运行、模块级队列保存待处理命令、运行结束后队列处理器自动出队、输入框上方显示 queued preview、Esc 取消当前 query。OneCode 中不需要 React hook；对应机制应落在 prompt_toolkit 的 `StreamingSession` 和 `InlineRepl` 调度上。

## Plan of Work

Milestone 1 先把队列模型从“字符串 deque”升级为正式 queued input。编辑 `ui/cli/terminal/queue.py`，新增一个小型 dataclass，例如 `QueuedInput`，表示一条等待执行的输入。它至少需要保存用户输入文本、输入类型、插入顺序和是否应在 preview 中显示。输入类型第一版只需要区分普通 prompt 与 slash command。队列仍然可以是单消费者 FIFO，消费者仍是 `InlineRepl`。这个 milestone 结束时，队列测试应证明空白输入不会入队，普通文本和 slash command 能被正确分类，`snapshot()` 是只读快照，`pop()` 按提交顺序返回对象而不是裸字符串。

Milestone 2 把运行中输入框放入 `StreamingSession`。编辑 `ui/cli/terminal/stream_session.py`，让 `StreamingSession` 接收当前 `InputQueue` 和可选 runtime，用于复用补全。`_build_app()` 中增加一个 prompt_toolkit buffer 区域，放在 live preview 和状态行附近，使同一个动态区应用同时显示 assistant/tool 状态和可编辑输入。Enter 的语义是：如果补全菜单正在选择完整 slash command，则先按既有补全规则处理；否则把当前 buffer 文本加入 `InputQueue`，立即清空 buffer，并继续保持 streaming app 运行。这个 milestone 结束时，测试应能用 `create_pipe_input()` 在 streaming session 运行期间发送 `第二个请求\r`，观察到 session 没有退出、队列新增一项、输入 buffer 被清空。

Milestone 3 增加 queued preview。编辑 `ui/cli/terminal/stream_view.py`，让 view 读取队列快照并渲染最多几条可见 queued input。预览应是暗色或低调文本，位于运行中输入框上方或状态区附近，不写入静态区。预览规则应简单：普通 prompt 显示 `queued: <text>`，slash command 显示同样的一行；文本过长时截断；超过可见数量时显示剩余数量摘要。这个 milestone 结束时，测试应构造带三条以上 queued input 的 queue，调用 view 渲染并断言可见条数和 overflow 摘要。

Milestone 4 让 `InlineRepl` 在 turn 结束后 drain queue。编辑 `ui/cli/terminal/repl.py::_run_turn()`，创建 `StreamingSession` 时传入同一个 `self._queue`。编辑 `_main_loop()` 中当前 drain 逻辑，使它消费 `QueuedInput` 对象。普通 prompt 出队后应调用 `print_user_submitted()` 并进入 `_run_turn(text)`；slash command 出队后应调用 `_handle_command(text)`，而不是把 slash command 交给模型。若 slash command 改变 runtime，例如 `/clear` 或 `/resume`，`InlineRepl` 应继续使用已有 `_reset_prompt_session()` 和 runtime 更新路径，并谨慎处理剩余队列。这个 milestone 结束时，测试应证明两条运行中输入会在当前 turn 后按顺序执行，queued slash command 会走命令分发而不是 agent loop。

Milestone 5 删除旧兼容路径并更新文档。编辑 `ui/cli/terminal/prompt_session.py`，删除 `queue_mode` 参数、`SubmissionKind.QUEUE` 和 Enter 时直接 `self._queue.push()` 的分支。`PromptSession` 只负责空闲态提交、取消和退出。删除或改写旧测试，例如只证明 `PromptSession(queue_mode=True)` 的测试不应保留。更新 `docs/design-docs/cli-architecture.md` 和 `docs/design-docs/cli-message-rendering-architecture.md`，说明运行中输入归 `StreamingSession`，空闲输入归 `PromptSession`，队列 drain 归 `InlineRepl`。这个 milestone 结束时，搜索 `queue_mode` 和 `SubmissionKind.QUEUE` 在生产代码中不应再出现。

Milestone 6 做验证和手动验收。运行聚焦测试和 compile 检查，然后启动真实 CLI 手动观察。手动场景是：启动 CLI，输入一个会触发工具或较长回答的请求；在 agent 输出期间继续输入第二条请求并按 Enter；确认输入框立即清空，动态区显示 queued preview；当前 turn 完成后，第二条请求自动作为用户输入执行。记录测试结果和任何终端显示问题。如果发现静态区和动态区撕裂，不要绕过 `TerminalOutputCoordinator` 打印队列预览，应调整 dynamic layout 或 prompt_toolkit invalidate 策略。

## Concrete Steps

从仓库根目录开始：

    cd D:\study\OneCode
    git status --short

如果工作树有与本计划无关的修改，不要回滚。只编辑本计划涉及的 CLI 文件和测试文件。先阅读当前文件：

    Get-Content ui\cli\terminal\queue.py
    Get-Content ui\cli\terminal\prompt_session.py
    Get-Content ui\cli\terminal\stream_session.py
    Get-Content ui\cli\terminal\stream_view.py
    Get-Content ui\cli\terminal\repl.py
    Get-Content tests\test_cli_terminal.py

实现 Milestone 1 后运行：

    uv run python -m pytest tests/test_cli_terminal.py -q

预期新增的队列测试通过。若现有测试因为旧 `InputQueue.pop()` 返回类型改变而失败，应更新测试到 `QueuedInput.text` 和 `QueuedInput.kind`，不要添加兼容返回字符串的路径。

实现 Milestone 2 和 Milestone 3 后运行：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_stream_view.py -q

预期结果是运行中输入测试能证明 `StreamingSession` 未结束时可以入队，view 测试能证明 queued preview 被渲染。若当前仓库没有 `tests/test_cli_stream_view.py`，可以把 view 相关测试放在已有 CLI streaming 测试文件中，但测试名应清楚表达 queued preview 行为。

实现 Milestone 4 后运行：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_commands.py -q

预期结果是 `InlineRepl` 能按队列顺序执行普通 prompt 和 slash command。测试应使用 fake runtime 或 monkeypatch `_run_turn()`、`_handle_command()` 记录调用顺序，不需要真实 provider。

实现 Milestone 5 后搜索旧路径：

    rg -n "queue_mode|SubmissionKind\.QUEUE|kind is SubmissionKind\.QUEUE" ui tests docs

生产代码中不应再有旧 queue mode 分支。文档中可以出现旧路径名称，但必须是在说明“已删除旧路径”的上下文里。最后运行：

    uv run python -m compileall ui core services
    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_stream_view.py tests/test_cli_commands.py -q

手动验收：

    uv run python -m ui.cli.app

在 CLI 中输入一个会持续一会儿的请求，例如：

    请搜索仓库里 CLI streaming 的实现，并读取相关文件

在输出还在进行时继续输入：

    总结一下刚才读到的文件职责

期望观察到：第二条输入按 Enter 后输入框立即清空；动态区显示一条 queued preview；第一条请求完成后，CLI 自动打印第二条用户输入并开始新的 agent turn。

## Validation and Acceptance

自动化验收必须证明四类行为。第一，队列模型正确：空白输入不入队，普通 prompt 和 slash command 被分类，`snapshot()` 不会被后续 push 改变，`pop()` 保持 FIFO。第二，运行中输入正确：`StreamingSession.run()` 正在消费事件时，用户按 Enter 能把输入加入队列，session 不结束，当前 agent 输出继续。第三，预览正确：动态区能显示 queued input，长文本被截断，多条队列有数量摘要，并且这些预览不写入静态 scrollback。第四，调度正确：当前 turn 完成后，`InlineRepl` drain 队列，普通 prompt 进入 `_run_turn()`，slash command 进入 `_handle_command()`，顺序与用户提交顺序一致。

聚焦测试命令：

    uv run python -m compileall ui core services
    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_stream_view.py tests/test_cli_commands.py -q

如果仓库中已有更细的 streaming session 测试文件，例如 `tests/test_cli_streaming_session_commit.py`，也应加入运行命令：

    uv run python -m pytest tests/test_cli_streaming_session_commit.py tests/test_streaming_coalescer.py -q

手动验收通过标准是用户能亲眼看到运行中输入框保持可编辑、Enter 后输入立即排队、queued preview 出现在动态区、当前 turn 完成后队列自动执行。失败标准包括：输入期间 agent 输出停止刷新、输入被写进静态 scrollback 但没有入队、队列预览残留到下一轮、slash command 被发送给模型、或旧 `queue_mode` 生产路径仍存在。

## Idempotence and Recovery

本计划可分 milestone 重复执行。每个 milestone 都应先运行聚焦测试，再继续下一步。不要使用 `git reset --hard` 或 `git checkout --` 回滚，因为工作树可能包含用户或其他 agent 的改动。若某一步失败，先运行 `git status --short` 查看实际改动范围，再用普通编辑修复相关文件。

删除旧兼容路径时要先保证新路径测试通过。若运行中输入框在真实终端中导致动态区重绘撕裂，不要通过 `print_static()` 或 Rich console 打印 queued preview 作为捷径；queued preview 必须留在 prompt_toolkit 动态区。若补全逻辑与运行中输入框复用困难，可以先把 slash command 补全降级为普通文本输入，但必须在 `Surprises & Discoveries` 和 `Decision Log` 记录原因，并保留后续恢复补全的明确任务。

## Artifacts and Notes

参考文件和快速定位关键词如下。实现者阅读这些文件时，应学习机制和职责分离，不要照搬 TypeScript/React 代码。

`docs/references/ui/utils/QueryGuard.ts` 展示同步 query 状态机。应学习 `idle -> dispatching -> running -> idle` 的状态含义，以及 `isActive` 作为“立即执行还是排队”的单一判断。快速搜索关键词：`class QueryGuard`、`reserve()`、`tryStart()`、`forceEnd()`、`get isActive`。

`docs/references/ui/utils/handlePromptSubmit.ts` 展示提交入口如何在 query 活跃时入队而不是执行。应学习“活跃时只允许部分模式入队、入队后立即清空输入框、队列处理路径跳过普通输入校验”的思路。快速搜索关键词：`queryGuard.isActive`、`enqueue({`、`queuedCommands?.length`、`queryGuard.reserve()`、`cancelReservation()`。

`docs/references/s04_hooks/useQueueProcessor.ts` 展示队列处理器如何等 query idle 后出队执行。应学习“处理器只在 query 不活跃、队列非空、没有本地 UI 阻塞时触发”的门槛。OneCode 中对应位置不是 React hook，而是 `InlineRepl` 在 `_run_turn()` 完成后的 drain 逻辑。快速搜索关键词：`useSyncExternalStore`、`queueSnapshot.length`、`processQueueIfReady`、`hasActiveLocalJsxUI`。

`docs/references/ui/components/PromptInput/PromptInputQueuedCommands.tsx` 展示 queued preview 的展示策略。应学习“只展示可见命令、隐藏系统 meta、最多展示若干条并折叠 overflow”的思路。OneCode 中对应位置是 `stream_view.py` 的动态区渲染。快速搜索关键词：`PromptInputQueuedCommands`、`isQueuedCommandVisible`、`MAX_VISIBLE_NOTIFICATIONS`、`processQueuedCommands`。

`docs/references/ui/screens/REPL.tsx` 展示 REPL 层如何把 `PromptInput` 永久挂在底部，同时 spinner/streaming 输出在上方变化。应学习“输入状态在 REPL 层持久存在、isLoading 时提交走队列、队列完成后 executeQueuedInput 再走正常 submit”的分层。快速搜索关键词：`useQueueProcessor`、`executeQueuedInput`、`queuedCommands.some`、`PromptInputQueuedCommands`、`<PromptInput`、`function onCancel()`。

`ui/cli/terminal/prompt_session.py` 是 OneCode 当前空闲输入实现。应学习已有 Enter/Tab 补全语义，并把可复用逻辑迁移给运行中输入框；迁移完成后删除 `queue_mode` 旧分支。快速搜索关键词：`queue_mode`、`SubmissionKind.QUEUE`、`_highlighted_completion`、`_apply_completion_for_edit`、`_build_key_bindings`。

`ui/cli/terminal/stream_session.py` 是 OneCode 运行中动态区入口。应在这里合并运行中输入框，而不是新建另一个并行 prompt_toolkit app。快速搜索关键词：`StreamingSession`、`_build_app`、`_feed`、`render_stream_body_ansi`、`render_status_fragments`。

`ui/cli/terminal/repl.py` 是队列最终执行的调度点。应让它在 turn 结束后消费 `InputQueue`，并区分普通 prompt 与 slash command。快速搜索关键词：`_main_loop`、`_run_turn`、`while self._queue`、`_handle_command`。

## Interfaces and Dependencies

不新增第三方依赖。继续使用 Python 标准库、prompt_toolkit 和 Rich。新行为应复用现有 `ui/cli/terminal` 分层：队列模型在 `queue.py`，运行中动态区在 `stream_session.py`，渲染在 `stream_view.py`，静态区输出仍只走 `TerminalOutputCoordinator`，主调度在 `repl.py`。

`ui/cli/terminal/queue.py` 在计划完成时应提供类似以下接口，具体字段名可微调，但职责不能退回裸字符串：

    @dataclass(frozen=True)
    class QueuedInput:
        text: str
        kind: Literal["prompt", "slash"]
        sequence: int
        visible: bool = True

    class InputQueue:
        def push(self, line: str) -> QueuedInput | None: ...
        def pop(self) -> QueuedInput | None: ...
        def snapshot(self) -> tuple[QueuedInput, ...]: ...
        def clear(self) -> None: ...

`ui/cli/terminal/stream_session.py::StreamingSession` 在计划完成时应能接收同一个 `InputQueue`：

    class StreamingSession:
        def __init__(self, *, queue: InputQueue, runtime: CliRuntime | None = None, ...): ...
        async def run(self, events, *, input=None, output=None) -> CliStreamUiState: ...

`ui/cli/terminal/stream_view.py` 应提供或扩展 view 函数，使 `StreamingSession` 能渲染 queued preview。它可以是独立 helper，也可以并入现有 body 渲染，但必须保持 view 无 I/O：

    def render_queued_inputs(queue_items: tuple[QueuedInput, ...], *, width: int) -> FormattedText | ANSI: ...

`ui/cli/terminal/repl.py::InlineRepl` 仍是唯一队列消费者。它应把同一个 `self._queue` 传给 `StreamingSession`，并在 `_run_turn()` 返回后 drain。drain 时必须区分 `QueuedInput.kind`，普通 prompt 走 `_run_turn()`，slash command 走 `_handle_command()`。

`ui/cli/terminal/prompt_session.py` 在计划完成时不再暴露 `queue_mode`。`PromptSession.read()` 应只表示空闲态读取用户输入：

    async def read(self, *, input=None, output=None) -> PromptSubmission: ...

## Revision Notes

- 2026-06-18 / Codex：创建本 ExecPlan，原因是用户要求参考 Claude 风格的输入框命令排队机制，为 OneCode 撰写符合 `PLANS.md` 的中文执行计划。本文明确采用替换式重构，把运行中输入框合并进 `StreamingSession`，迁移完成后删除旧 `PromptSession.queue_mode` 兼容路径。
