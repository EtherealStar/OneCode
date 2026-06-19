# 重构 OneCode CLI 实时流式渲染状态与输出协调

本 ExecPlan 是一个 living document，也就是会随着实现推进持续更新的执行计划。`进度（Progress）`、`意外发现（Surprises & Discoveries）`、`决策记录（Decision Log）` 和 `结果与回顾（Outcomes & Retrospective）` 必须在实现过程中保持最新。

本文档遵循仓库根目录下的 `PLANS.md`。任何实现或修订本计划的人都必须保持它自包含，并在决策和结果变化时同步更新所有 living sections。本文档是一个替换式重构计划：实现过程中应删除旧的混合渲染路径，不为了迁移安全保留旧代码兼容分支。本文档只描述功能目标、模块职责、函数名、状态流和测试验收，不写详细实现代码。

## 目的与全局效果（Purpose / Big Picture）

完成此变更后，OneCode 的交互式 CLI 在模型输出文本、声明工具调用、执行多个工具、写入工具结果时，会把“助手文本”“工具状态”“底部状态行”“最终静态 scrollback”分成明确的 UI 状态和输出阶段。用户在 `uv run python -m ui.cli.app` 中触发工具调用时，不再看到助手文本和工具调用渲染融合在一起，也不会在底部 `thinking...` 附近看到多个工具行以错位方式残留。

当前截图问题的根源不是 agent 主循环不实时，而是 CLI 渲染层把 assistant 文本尾部、active tools 和 status line 混在一个动态区里，同时工具结果又从动态区运行期间直接写入静态 scrollback。这个重构会引入一个 CLI 层流式 UI reducer 和一个终端输出协调器，让所有静态写入都经过统一调度，让动态区只负责显示当前 turn 的临时状态。

验收方式是启动交互式 CLI，输入一个会触发 `grep`、`glob` 或 `read_file` 等工具的请求。动态区域应先显示流式助手文本，再显示独立的工具状态面板，底部状态行只显示当前阶段；工具结果应该在安全时机进入 scrollback，不能插入到还未擦除的动态区内部。

## 进度（Progress）

- [x] (2026-06-17 00:00+08:00) 已阅读 `PLANS.md`，确认本计划必须自包含、可执行、持续更新 living sections。
- [x] (2026-06-17 00:00+08:00) 已调查现有 CLI 渲染链路，定位到 `ui/cli/terminal/stream_session.py`、`ui/cli/terminal/turn_render_state.py` 和 `ui/cli/terminal/static_output.py` 是截图问题的主要区域。
- [x] (2026-06-17 00:00+08:00) 已对照 Claude Code 参考实现，确认 `docs/references/ui/utils/messages.ts::handleMessageFromStream` 的事件分流模式、`docs/references/ui/screens/REPL.tsx` 的独立 streaming state、`docs/references/ui/components/messages/AssistantToolUseMessage.tsx` 的工具三态是本次设计的主要参考。
- [x] (2026-06-17 00:00+08:00) 已撰写本 ExecPlan 初稿。
- [x] (2026-06-17 00:00+08:00) Milestone 1：实现 `ui/cli/terminal/stream_state.py`（`CliStreamUiState`、`StreamingToolUseState`、`CompletedToolCommit`、`StreamMode`、`ToolStatus`）和 `ui/cli/terminal/stream_reducer.py`（`reduce_stream_event`），新增 `tests/test_cli_stream_reducer.py`（24 tests pass）。reducer 纯函数无 I/O。
- [x] (2026-06-17 00:00+08:00) Milestone 2：实现 `ui/cli/terminal/stream_view.py`（`render_stream_body_ansi` + `render_status_fragments`），新增 `tests/test_cli_stream_view.py`（15 tests pass）。Body 在 assistant 段和 tool 段之间插入空行；状态行从 `stream_mode` + active tool 推导，运行工具时不再显示裸 `thinking…`。
- [x] (2026-06-17 00:00+08:00) Milestone 3：实现 `ui/cli/terminal/output_coordinator.py`（`TerminalOutputCoordinator`），新增 `tests/test_cli_output_coordinator.py`（11 tests pass）。`queue_*` 不写 stdout；`flush_static_commits` 是唯一静态写入路径。
- [x] (2026-06-17 00:00+08:00) Milestone 4：重写 `ui/cli/terminal/stream_session.py` —— `StreamingSession` 内部改为持有 `CliStreamUiState` + `StreamingCoalescer` + `TerminalOutputCoordinator`；删除 `ui/cli/terminal/turn_render_state.py`；删除 `tests/test_cli_turn_render_state.py` 和 `tests/test_streaming_markdown_state.py`；重写 `tests/test_cli_streaming_session_commit.py` 和 `tests/test_cli_terminal.py` 中相关测试，使用新 API。
- [x] (2026-06-17 00:00+08:00) Milestone 5：更新 `docs/design-docs/cli-message-rendering-architecture.md` 和 `docs/design-docs/cli-architecture.md`；运行 `uv run python -m compileall ui services core`（无错误）；运行聚焦回归（131 tests pass）；运行 `rg “TurnRenderState|StreamBuffer|render_turn_preview_ansi|consume_agent_event” ui` 仅剩 docstring 内的历史描述，无生产代码引用。

## 意外发现（Surprises & Discoveries）

- Observation: OneCode 的 `core/loop.py` 已经逐事件实时转发，并不是截图问题的主要根源。
  Evidence: `core/loop.py` 在模型流里把 `content_delta` 立即转换成 `assistant_delta`，把 `tool_call_completed` 立即转换成 `tool_call_ready`，并在工具执行期间继续产出 `tool_started`、`tool_progress`、`tool_result`。

- Observation: 当前 `StreamingSession._feed` 在动态 preview app 仍运行时调用 `_flush_completed_tools_to_static()`，而该函数会直接调用 `print_tool_result()` 写 Rich 静态输出。
  Evidence: `ui/cli/terminal/stream_session.py::_feed` 在处理每个事件后调用 `_flush_completed_tools_to_static()`；`_flush_completed_tools_to_static()` 直接调用 `ui/cli/terminal/static_output.py::print_tool_result()`。

- Observation: 当前动态区把 assistant tail 和 active tools 拼在同一个 renderable 中，视觉上没有强边界。
  Evidence: `ui/cli/terminal/turn_render_state.py::render_turn_preview_ansi` 先把 `state.assistant.text` 渲染为尾部行，再把 `state.visible_active_tools()` 的工具行追加到同一个 `out_lines`。

- Observation: Claude Code 的参考实现不是把所有流事件塞进一个视图对象，而是通过 `handleMessageFromStream()` 把事件分发到多个独立 UI state。
  Evidence: `docs/references/ui/utils/messages.ts::handleMessageFromStream` 接受 `onMessage`、`onUpdateLength`、`onSetStreamMode`、`onStreamingToolUses`、`onStreamingThinking`、`onStreamingText` 等回调；`docs/references/ui/screens/REPL.tsx` 分别维护 `streamMode`、`streamingToolUses`、`streamingText`、`inProgressToolUseIDs`。

- Observation: 重构实施时 `tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt` 和 `tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts` 已经在 main 分支上失败（tool prompt 模板重构相关），与本次流式渲染重构无关。
  Evidence: `git stash` 后 `uv run python -m pytest` 同样报这两个失败；`git stash pop` 还原后失败依旧。基线证据：见原 main 提交 b9b459e。
  Action: 不计入本次重构结果；后续 test owner 处理。

## 决策记录（Decision Log）

- Decision: 本次重构只改变 CLI UI 层，不修改 `core/loop.py`、工具 executor、provider adapter 或 message store 的核心语义。
  Rationale: 截图问题由终端渲染状态和输出协调引起。保持 core loop 不变能保护 OneCode “主循环保持薄”的架构边界。
  Date/Author: 2026-06-17 / Codex。

- Decision: 使用替换式重构，删除旧的 `StreamBuffer` / `TurnRenderState` 混合渲染路径，不保留新旧双路径开关。
  Rationale: 用户明确要求这是大的重构，不能为了迁移式安全保留旧代码兼容。旧路径本身就是问题来源，保留会让输出路径继续分叉并增加回归风险。
  Date/Author: 2026-06-17 / Codex。

- Decision: 借鉴 Claude Code 的”事件分流为多个 UI 状态”，但不引入 React、Ink 或虚拟 DOM。
  Rationale: OneCode 当前 CLI 基于 `prompt_toolkit` 和 Rich。截图问题可以在现有终端栈内解决，重写为 Ink 风格渲染器会扩大范围，且不符合当前 Python CLI 架构。
  Date/Author: 2026-06-17 / Codex。

- Decision: 所有静态 scrollback 写入必须经过一个输出协调器，不允许动态区运行期间从任意 reducer 或 flush helper 直接调用 `print_static()`。
  Rationale: 当前融合问题的关键就是 prompt_toolkit 动态区擦除/重绘与 Rich 静态输出并发写 stdout。集中调度可以让写入顺序和安全点可测试。
  Date/Author: 2026-06-17 / Codex。

- Decision: view 在 assistant 段和 tool panel 段之间插入空行作为视觉边界。
  Rationale: 用户截图问题中”assistant 文本和工具调用渲染融合”的根因之一是两段在同一 `out_lines` 拼接，窄终端下没有强边界。空行是简单且通用的稳定分隔，与宽度无关。
  Date/Author: 2026-06-17 / Codex。

- Decision: 状态行从 `stream_mode` + active tool 集合推导，而不是单独维护 `current_tool_label`。
  Rationale: 旧的 `current_tool_label` 是 turn 级别的”最近一个工具名”，在多工具并行时会泄漏名字到不相关的工具。新设计里 `render_status_fragments` 只读 `state.tools` 中当前活跃的工具，因此多个工具运行时会显示 `tools: N running`，符合 Claude Code 参考实现的”stable tool label, not last tool name”语义。
  Date/Author: 2026-06-17 / Codex。

- Decision: `TerminalOutputCoordinator` 保留 `begin_dynamic_app` / `end_dynamic_app` advisory 标志，flush 行为对当前状态不敏感。
  Rationale: 当前 `run()` 在动态 app 退出后才 flush，因此 advisory 标志不参与决策；保留它是为了未来接入 `prompt_toolkit.run_in_terminal` 临时挂起动态区时，不需要修改公共 API。tests 不依赖这个标志。
  Date/Author: 2026-06-17 / Codex。

## 结果与回顾（Outcomes & Retrospective）

Milestone 1-5 全部完成。自动化测试结果：

- `tests/test_cli_stream_reducer.py`：24 tests pass。
- `tests/test_cli_stream_view.py`：15 tests pass。
- `tests/test_cli_output_coordinator.py`：11 tests pass。
- `tests/test_cli_streaming_session_commit.py`：8 tests pass（重写为新 API）。
- `tests/test_cli_terminal.py`：74 tests pass（重写 M4 段测试为新 API）。
- `tests/test_streaming_coalescer.py`：8 tests pass（无修改，沿用旧测试）。
- `tests/test_loop_realtime_streaming.py`：1 test pass（无修改）。
- 合计聚焦回归：131 tests pass。

代码层结果：

- 新增 `ui/cli/terminal/stream_state.py`（状态模型）。
- 新增 `ui/cli/terminal/stream_reducer.py`（纯 reducer）。
- 新增 `ui/cli/terminal/stream_view.py`（动态区 view）。
- 新增 `ui/cli/terminal/output_coordinator.py`（静态区协调器）。
- 重写 `ui/cli/terminal/stream_session.py`，内部持有三个新组件。
- 删除 `ui/cli/terminal/turn_render_state.py`。
- 删除 `tests/test_cli_turn_render_state.py` 和 `tests/test_streaming_markdown_state.py`。
- 重写 `tests/test_cli_streaming_session_commit.py` 和 `tests/test_cli_terminal.py` 中受影响的测试。
- `uv run python -m compileall ui services core`：无错误。
- `rg “TurnRenderState|StreamBuffer|render_turn_preview_ansi|consume_agent_event” ui`：仅剩 docstring 内的历史描述，生产代码无引用。

未在本会话内做交互式手动验收（截图需 GUI 终端），但测试已经覆盖：

- assistant 段与 tool panel 段之间存在空行（`test_body_inserts_blank_line_between_assistant_and_tools`）。
- 状态行在有运行工具时绝不显示裸 `thinking…`（`test_status_shows_tool_label_when_tool_running`）。
- coordinator 的 `queue_*` 不写 stdout，仅 `flush_static_commits` 写（11 tests）。

未处理的预先存在失败：`tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt` 和 `tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts`，已在 `Surprises & Discoveries` 记录基线。

## 背景和定位（Context and Orientation）

OneCode 是一个 Python code agent runtime。`core/loop.py::AgentLoop` 是 agent 主循环，它接收用户输入、调用模型、转发模型流事件、执行工具、把工具结果写回消息链。CLI 只是 UI 层，位于 `ui/cli/`，不应该实现模型协议、工具执行或权限策略。

当前交互式 CLI 的入口是 `ui/cli/terminal/repl.py::InlineRepl`。用户提交输入后，`InlineRepl._run_turn()` 创建 `ui/cli/terminal/stream_session.py::StreamingSession`，把 `runtime.loop.stream()` 产生的 `AgentEvent` 交给 `StreamingSession.run()`。`AgentEvent` 是 OneCode 内部的流式事件对象，定义在 `core/stream_events.py`，常见类型包括 `assistant_delta`、`assistant_message_completed`、`tool_call_ready`、`tool_started`、`tool_progress`、`tool_result`、`transition`、`completed` 和 `error`。

当前动态区由 `prompt_toolkit.Application(full_screen=False, erase_when_done=True)` 管理。动态区是一个可擦除的屏幕底部区域，用来显示流式 preview 和状态行；静态区是普通终端 scrollback，由 `ui/cli/terminal/static_output.py` 中的 Rich `Console.print()` 写入。动态区内容结束后会被擦除，静态区内容会永久留在终端滚动历史里。

当前问题路径是：`stream_session.py::consume_event()` 把事件折叠进 `StreamBuffer.turn_state`；`turn_render_state.py::render_turn_preview_ansi()` 把 assistant 文本尾部和 active tool 行拼接成同一个动态区 body；`stream_session.py::_flush_completed_tools_to_static()` 在事件循环中直接把 completed tool result 写入静态区。由于 prompt_toolkit 仍持有动态区，Rich 静态输出可能和动态区擦除/重绘交错。

Claude Code 参考实现提供了更清晰的 UI 状态分层。`docs/references/ui/utils/messages.ts::handleMessageFromStream` 把单个流事件分发到多个回调；`docs/references/ui/screens/REPL.tsx` 用独立 React state 保存 `streamingText`、`streamingToolUses`、`streamMode`、`inProgressToolUseIDs`；`docs/references/ui/components/messages/AssistantToolUseMessage.tsx` 通过 `isQueued`、`inProgressToolUseIDs` 和 `isResolved` 区分工具的排队、运行、完成状态。本计划会借鉴这些状态边界，而不是复制 React/Ink 实现。

本文使用几个术语。动态区指 prompt_toolkit 可擦除区域。静态区指终端 scrollback。reducer 指一个只接收事件并更新内存状态的函数，它不读写 stdout、不运行工具、不访问文件。输出协调器指一个集中管理“何时把内容写到静态区、何时让动态区重绘、何时退出动态 app”的对象。

## 工作方案（Plan of Work）

第一步是建立新的流式 UI 状态模型。新增 `ui/cli/terminal/stream_state.py`，定义 `CliStreamUiState`、`StreamingTextState`、`StreamingToolUseState`、`CompletedToolCommit` 和 `StreamMode`。`CliStreamUiState` 是一个 turn 内的临时 UI 状态，不是模型上下文事实来源。它保存当前流式文本、当前工具调用、已完成但还未写入 scrollback 的工具结果、状态行阶段和错误文本。

同一阶段新增 `ui/cli/terminal/stream_reducer.py`，定义 `reduce_stream_event(state, event)`。这个 reducer 必须是纯函数风格：它只修改传入的 `CliStreamUiState`，不调用 `print_static()`、不调用 Rich `Console`、不退出 prompt_toolkit app。事件映射按以下思路实现：`assistant_delta` 追加到 `streaming_text`；`tool_call_ready` 创建 queued 工具状态；`tool_started` 将对应工具改为 running；`tool_progress` 更新工具进度；`tool_result` 将工具改为 completed 并追加一个 `CompletedToolCommit`；`completed` 和 `transition` 只更新阶段信息，不直接打印。

第二步是重写动态区布局。新增或替换 `ui/cli/terminal/stream_view.py`，提供 `render_stream_body_ansi(state, width)` 和 `render_status_fragments(state)`。body 分成两个视觉段：assistant preview 段只显示 assistant 文本尾部；tool panel 段显示 queued/running 工具，最多显示三条，多余折叠成一条摘要。两段之间必须有空行或稳定分隔，避免工具行紧贴文本尾部。status line 从 `state.stream_mode` 和活跃工具集合推导，不再使用单个 `current_tool_label`。当仍有运行工具时，status line 不得显示单纯 `thinking...`。

第三步是引入输出协调器。新增 `ui/cli/terminal/output_coordinator.py`，定义 `TerminalOutputCoordinator`。这个对象拥有两类方法：一类接收 pending commit，例如 `queue_tool_result(result, call_id)` 和 `queue_assistant_markdown(text)`；另一类在安全点把队列写入静态区，例如 `flush_static_commits()`。安全点的定义是：动态 app 退出之后，或者通过 prompt_toolkit 的 terminal-safe 机制临时暂停动态区后。实现者应优先使用 prompt_toolkit 提供的 `run_in_terminal` 思路，但具体函数封装在 coordinator 内部，其他模块不能直接使用。

第四步是重写 `ui/cli/terminal/stream_session.py`。删除旧 `StreamBuffer`、`consume_event()`、`render_preview_ansi()`、`commit_final()` 中依赖旧混合状态的实现。`StreamingSession` 持有 `CliStreamUiState`、`StreamingCoalescer` 和 `TerminalOutputCoordinator`。`_feed()` 的事件循环流程改为：先通过 coalescer 合并高频事件，再调用 `reduce_stream_event()`，然后把 reducer 产生的 pending commits 交给 coordinator；动态区只根据 `stream_view.py` 渲染当前 state。`_feed()` 不能直接调用 `print_tool_result()`。`run()` 结束时由 coordinator 统一提交最终 assistant markdown 和剩余工具结果。

第五步是删除 `ui/cli/terminal/turn_render_state.py` 中不再需要的混合 preview 逻辑。如果新的 `stream_state.py` 完全覆盖其职责，应删除该文件和对应旧测试；如果 `markdown_rendering.py` 中的缓存函数仍有价值，则保留 `markdown_rendering.py` 和 `text_cache.py`，但不保留 `TurnRenderState` 作为兼容层。现有测试中引用 `StreamBuffer`、`consume_event`、`TurnRenderState`、`render_turn_preview_ansi` 的用例必须改写为新状态模型测试，而不是通过 shim 继续兼容。

第六步是更新设计文档。修改 `docs/design-docs/cli-message-rendering-architecture.md`，说明新的流式渲染流为 `AgentEvent -> stream_reducer -> CliStreamUiState -> stream_view -> TerminalOutputCoordinator`。旧文档里“工具事件写静态区”和“TurnRenderState 是单一动态区事实来源”的描述要删除或改写。若 `docs/design-docs/cli-architecture.md` 中描述了 `StreamingSession` 行为，也要同步调整。

## 里程碑（Milestones）

### Milestone 1: 新状态模型和 reducer

这一阶段只建立状态模型和事件转换，不触碰真实终端输出。完成后，测试可以直接构造 `AgentEvent` 序列并断言 `CliStreamUiState` 的字段变化。要创建 `ui/cli/terminal/stream_state.py` 和 `ui/cli/terminal/stream_reducer.py`，并新增 `tests/test_cli_stream_reducer.py`。

验收标准是：一个事件序列 `assistant_delta -> tool_call_ready -> tool_started -> tool_progress -> tool_result` 会得到独立的 `streaming_text`、`tools[call_id].status` 和 `pending_static_commits`。reducer 不产生任何 stdout 输出。可通过运行以下命令验证：

    cd D:\study\OneCode
    uv run python -m pytest tests/test_cli_stream_reducer.py -q

### Milestone 2: 新动态区 view

这一阶段让新的 state 可以渲染成 prompt_toolkit 可显示的 ANSI 文本，但仍不接入真实 `StreamingSession`。要创建 `ui/cli/terminal/stream_view.py`，并新增 `tests/test_cli_stream_view.py`。测试应覆盖 assistant 文本与工具面板之间有明确边界、多个 queued/running 工具折叠、status line 从活跃工具推导。

验收标准是：当 state 同时包含 assistant 文本和两个 active tools 时，渲染结果里 assistant 段和 tool 段不会在同一行融合；当 active tools 非空时 status 不显示裸 `thinking...`。运行：

    cd D:\study\OneCode
    uv run python -m pytest tests/test_cli_stream_view.py -q

### Milestone 3: 输出协调器

这一阶段解决 stdout 写入竞争。要创建 `ui/cli/terminal/output_coordinator.py`，把工具结果和最终 assistant markdown 的静态提交收敛到一个对象中。测试使用 StringIO 绑定静态 console，验证动态运行期间调用 queue 方法不会立刻写入 scrollback，调用 flush 后才按顺序写入。

验收标准是：`queue_tool_result()` 后捕获的静态输出仍为空；`flush_static_commits()` 后输出包含工具摘要；多个 commits 按事件顺序输出且不会重复。运行：

    cd D:\study\OneCode
    uv run python -m pytest tests/test_cli_output_coordinator.py -q

### Milestone 4: 替换 StreamingSession 并删除旧路径

这一阶段把新 reducer、view、coordinator 接入 `ui/cli/terminal/stream_session.py`，并删除旧的混合状态路径。不要保留 `StreamBuffer`、旧 `consume_event()`、旧 `commit_final()` 的兼容 API；对应测试必须迁移到新接口。`StreamingCoalescer` 可以保留，因为它是事件合并器，不是问题来源。

验收标准是：现有 CLI streaming 测试被改写后通过；搜索仓库不应再出现生产代码引用 `TurnRenderState`、`StreamBuffer` 或 `render_turn_preview_ansi`。运行：

    cd D:\study\OneCode
    rg "TurnRenderState|StreamBuffer|render_turn_preview_ansi|consume_event" ui tests
    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_streaming_session_commit.py tests/test_streaming_coalescer.py -q

`rg` 的期望结果是只在已删除文件不存在，或仅在本 ExecPlan / 历史文档中出现；生产代码和新测试不能继续依赖旧路径。

### Milestone 5: 文档、回归和手动验收

这一阶段更新 `docs/design-docs/cli-message-rendering-architecture.md` 和必要的 CLI 架构文档，运行聚焦回归，再启动真实 CLI 做一次手动观察。手动场景应选择能触发多个工具调用的请求，例如“搜索仓库里 bash 工具相关文件并读两个关键文件”。

验收标准是：自动化测试通过；交互式 CLI 中 assistant 文本、工具状态面板和底部状态行视觉上分离；工具结果进入 scrollback 时不会挤进动态区；多个工具进行时 status line 显示工具状态而不是错误的 `thinking...`。运行：

    cd D:\study\OneCode
    uv run python -m compileall ui services core
    uv run python -m pytest tests/test_cli_stream_reducer.py tests/test_cli_stream_view.py tests/test_cli_output_coordinator.py tests/test_cli_terminal.py tests/test_cli_streaming_session_commit.py tests/test_streaming_coalescer.py tests/test_loop_realtime_streaming.py tests/test_cli_tool_renderers.py -q
    uv run python -m ui.cli.app

## 具体步骤（Concrete Steps）

从 `D:\study\OneCode` 开始。先新建 `ui/cli/terminal/stream_state.py`，定义 turn 内临时状态。建议字段包括 `streaming_text`、`tools`、`completed_commits`、`stream_mode`、`error_text`、`assistant_completed`、`turn_completed`。工具状态使用稳定的 `call_id` 作为 key，状态值使用字符串或枚举表达 `queued`、`running`、`completed`、`error`。

然后新建 `ui/cli/terminal/stream_reducer.py`。实现 `reduce_stream_event(state, event)`，只负责事件到状态的转换。不要在 reducer 中 import `ui.cli.terminal.static_output`，不要创建 Rich `Console`，不要调用 `print_tool_result()`。为这个 reducer 写 `tests/test_cli_stream_reducer.py`。

接着新建 `ui/cli/terminal/stream_view.py`。这里可以复用 `ui/cli/terminal/markdown_rendering.py::render_cached_markdown` 渲染 assistant 文本尾部，但不要重新引入旧 `TurnRenderState`。`render_stream_body_ansi(state, width)` 返回 prompt_toolkit `ANSI`；`render_status_fragments(state)` 返回 prompt_toolkit `FormattedText` 片段。为它写 `tests/test_cli_stream_view.py`。

再新建 `ui/cli/terminal/output_coordinator.py`。协调器内部调用 `ui.cli.terminal.static_output.print_tool_result` 和 `print_assistant_markdown`，但外部只能 queue 和 flush。把“是否动态 app 正在运行”的知识封装在 coordinator 或 `StreamingSession` 内。为它写 `tests/test_cli_output_coordinator.py`。

随后重写 `ui/cli/terminal/stream_session.py`。保留 `StreamingSession` 作为外部入口，但内部状态改为新的 `CliStreamUiState`。`_build_app()` 的 `preview_text()` 调用 `stream_view.render_stream_body_ansi()`，`status_text()` 调用 `stream_view.render_status_fragments()`。`_feed()` 继续使用 `StreamingCoalescer`，但 coalescer 的 apply 目标改为 reducer。任何 completed tool result 都通过 coordinator queue，不直接打印。

完成接入后，删除旧状态文件或旧状态类。若 `ui/cli/terminal/turn_render_state.py` 已无生产引用，应删除该文件。若某些小 helper 仍有价值，应移动到新模块并删除旧文件，不能保留 `TurnRenderState` 名称做兼容包装。更新所有测试引用。

最后更新 `docs/design-docs/cli-message-rendering-architecture.md`。文档必须说明静态区写入现在由 `TerminalOutputCoordinator` 统一管理，动态区只渲染 `CliStreamUiState`。旧的“工具事件写静态区”和“工具结果由 `_flush_completed_tools_to_static` 直接打印”的描述必须移除。

## 验证和验收（Validation and Acceptance）

自动化验证分三层。第一层是纯状态测试：`tests/test_cli_stream_reducer.py` 和 `tests/test_cli_stream_view.py` 不需要真实终端，只验证事件和渲染文本。第二层是输出协调测试：`tests/test_cli_output_coordinator.py` 用捕获的 Rich console 验证 queue 和 flush 顺序。第三层是集成回归：`tests/test_cli_terminal.py`、`tests/test_cli_streaming_session_commit.py`、`tests/test_streaming_coalescer.py` 和 `tests/test_loop_realtime_streaming.py` 验证真实 `StreamingSession` 消费事件时不丢文本、不重复提交工具结果、不破坏 loop 实时性。

完整聚焦命令是：

    cd D:\study\OneCode
    uv run python -m compileall ui services core
    uv run python -m pytest tests/test_cli_stream_reducer.py tests/test_cli_stream_view.py tests/test_cli_output_coordinator.py tests/test_cli_terminal.py tests/test_cli_streaming_session_commit.py tests/test_streaming_coalescer.py tests/test_loop_realtime_streaming.py tests/test_cli_tool_renderers.py -q

通过标准是 compileall 无错误，pytest 全部通过。若仓库存在与本计划无关的既有失败，必须在 `Surprises & Discoveries` 中记录失败测试名、失败原因和基线证据，不能把它混入本重构结果。

手动验收从 `D:\study\OneCode` 运行：

    uv run python -m ui.cli.app

输入一个能触发多个工具的请求，例如：

    请查看你自己的 bash 工具文件

预期观察是：动态区上半部分只显示 assistant 文本 preview；工具状态显示在独立 tool panel，不和 assistant 文本同一行；底部 status line 在有工具时显示 `tool: ...` 或 `tools: ...`，不显示错误的 `thinking...`；工具完成摘要写入 scrollback 后，动态区不会残留旧工具行，也不会把静态结果插入到动态 preview 中。

## 幂等性与恢复（Idempotence and Recovery）

本计划的实现步骤可以重复执行，但这是替换式重构，不应同时保留新旧生产路径。如果 Milestone 4 中删除旧文件后测试失败，应修正新路径或测试，不应通过恢复旧 `StreamBuffer` / `TurnRenderState` 兼容层来通过测试。

如果某个阶段中断，先运行 `git status` 查看已改文件，再从最近的 Milestone 测试继续。新增模块可以重复创建或修改。删除旧文件前必须确认 `rg "TurnRenderState|StreamBuffer|render_turn_preview_ansi"` 的生产代码引用已经迁移。不要使用 `git reset --hard` 回滚用户或其他工作；如需撤销本计划相关改动，使用文件级补丁反向修改。

静态 console 捕获测试可能受全局 `_STATIC_CONSOLE` 影响。每个测试应调用 `ui.cli.terminal.static_output.reset_static_console()` 或使用已有测试 fixture 重新绑定 console，避免测试之间污染。

## 参考材料和备注（Artifacts and Notes）

本计划参考的 Claude Code 文件和用途如下：

- `docs/references/ui/utils/messages.ts::handleMessageFromStream`：参考事件分流方式。它将一个 stream event 同时驱动 message list、streaming text、streaming tool uses、stream mode 和 thinking state。
- `docs/references/ui/screens/REPL.tsx`：参考 REPL 层如何保存 `streamingText`、`streamingToolUses`、`streamMode`、`inProgressToolUseIDs`，以及如何把这些状态传给 `<Messages>` 和 spinner。
- `docs/references/ui/components/Messages.tsx`：参考 streaming tool use 如何作为临时 UI 内容进入消息渲染，而不是污染已提交历史。
- `docs/references/ui/components/messages/AssistantToolUseMessage.tsx`：参考工具 queued、running、resolved 三态。
- `docs/references/ui/components/Markdown.tsx::StreamingMarkdown`：参考稳定前缀和不稳定后缀的 Markdown 渲染思路。OneCode 已有 `markdown_rendering.py` 和 `text_cache.py`，本次不需要重新设计 Markdown 缓存。
- `docs/references/Tools_full/services/tools/StreamingToolExecutor.ts`：仅作为后续工具执行优化参考。本计划不实现“模型仍在生成时就开始执行工具”。

当前 OneCode 文件和处理方向如下：

- `ui/cli/terminal/stream_session.py`：保留 `StreamingSession` 入口，重写内部状态和输出流程。
- `ui/cli/terminal/turn_render_state.py`：删除或拆迁，不保留旧类兼容。
- `ui/cli/terminal/static_output.py`：保留底层静态打印函数，但只允许 coordinator 调用它们。
- `ui/cli/terminal/streaming_coalescer.py`：保留，用于高频事件合并。
- `ui/cli/terminal/markdown_rendering.py` 和 `ui/cli/terminal/text_cache.py`：保留，用于 assistant 文本渲染和缓存。
- `docs/design-docs/cli-message-rendering-architecture.md`：实现完成后同步改写。

## 接口和依赖（Interfaces and Dependencies）

本重构不新增第三方依赖。继续使用标准库、Rich 和 prompt_toolkit。新接口集中在 `ui/cli/terminal/`。

在 `ui/cli/terminal/stream_state.py` 中定义：

    class StreamMode:
        requesting/responding/tool_input/tool_running/awaiting_model/completed/error

    @dataclass
    class StreamingToolUseState:
        call_id: str
        tool_name: str
        status: str
        input_preview: str
        progress: str
        result: object | None

    @dataclass
    class CompletedToolCommit:
        call_id: str
        result: object
        committed: bool = False

    @dataclass
    class CliStreamUiState:
        streaming_text: str
        tools: dict[str, StreamingToolUseState]
        pending_commits: list[CompletedToolCommit]
        stream_mode: str
        error_text: str
        assistant_completed: bool
        turn_completed: bool

具体字段名可以在实现时微调，但必须表达这些职责，且不能重新合并成旧的 `TurnRenderState`。

在 `ui/cli/terminal/stream_reducer.py` 中定义：

    def reduce_stream_event(state: CliStreamUiState, event: AgentEvent) -> None

它是唯一的事件到 UI 状态转换入口。它不产生 I/O。

在 `ui/cli/terminal/stream_view.py` 中定义：

    def render_stream_body_ansi(state: CliStreamUiState, *, width: int) -> ANSI
    def render_status_fragments(state: CliStreamUiState) -> FormattedText

它只负责把 state 变成动态区可显示文本，不提交静态 scrollback。

在 `ui/cli/terminal/output_coordinator.py` 中定义：

    class TerminalOutputCoordinator:
        def queue_tool_result(self, result: object, *, call_id: str) -> None: ...
        def queue_assistant_markdown(self, text: str) -> None: ...
        def flush_static_commits(self) -> None: ...

它是唯一允许调用 `print_tool_result()` 和 `print_assistant_markdown()` 的流式会话组件。`static_output.py` 本身仍可被 slash command、banner 等非流式路径使用，但 `StreamingSession` 内部不得绕过 coordinator。

## 修订记录（Revision Note）

2026-06-17 / Codex：创建本 ExecPlan，原因是用户提供 Claude Code 实时流式输出架构作为参考，并要求按 `PLANS.md` 用中文撰写一个大的替换式重构计划，解决当前 CLI 截图中 assistant 文本与工具调用渲染融合、底部 thinking 区域显示多个工具进行时的问题。

2026-06-17 / Codex：将模板句和主要章节标题改为中文，并保留英文 section 名称作为括号标识，原因是用户明确要求中文计划，同时 `PLANS.md` 要求这些 living sections 必须存在且可被后续执行者识别。
