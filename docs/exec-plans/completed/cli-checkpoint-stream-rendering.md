# 重构 CLI checkpoint 流式渲染与工具结果时序提交

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵循仓库根目录 `PLANS.md`。任何实现者都必须把本文作为可执行规格维护：推进实现时同步更新进度、发现、决策和结果；本文必须始终保持自包含，不能依赖对话历史。

## Purpose / Big Picture

当前交互式 CLI 的动态区会实时显示 assistant 文本和工具状态，但工具结果摘要会被 `TerminalOutputCoordinator.flush_static_commits()` 延后到整个 turn 结束后才写入终端 scrollback。用户看到的效果是：工具已经完成，动态区仍在变化，但历史区没有立即出现已完成工具的摘要；等最终 assistant 输出结束后，工具结果和最终文本一起进入静态区，时序感不清晰。

本重构完成后，CLI 会按 checkpoint 提交历史。checkpoint 指“一个可以定稿并写入终端 scrollback 的边界”：assistant 本轮消息完成后立即静态化；该 assistant 声明的工具结果完成且满足顺序约束后立即静态化；下一轮 assistant 文本继续在新的动态区流式显示。动态区始终只显示尚未定稿的当前片段，已经完成的片段会被提交到静态区，新的动态区在最新静态历史下面继续移动，避免静态区和动态区融合。

用户可以通过运行 `uv run python -m ui.cli.app` 并输入一个会触发工具的请求观察效果。预期行为是：assistant 先流式显示；模型决定调用工具后，该 assistant 文本进入 scrollback；工具运行状态继续在动态区显示；工具完成后，其结果摘要按工具声明顺序进入 scrollback；随后下一轮 assistant 文本继续流式显示在新的动态区。

## Progress

- [x] (2026-06-17 00:00+08:00) 已阅读 `PLANS.md`，确认 ExecPlan 必须自包含、可执行、living sections 必须持续更新。
- [x] (2026-06-17 00:00+08:00) 已阅读现有 CLI 渲染文档 `docs/design-docs/cli-message-rendering-architecture.md`，确认当前工具结果通过 `TerminalOutputCoordinator` 队列延后到 turn 结束 flush。
- [x] (2026-06-17 00:00+08:00) 已阅读用户提供的 Claude 时序参考材料，提炼出三条可迁移原则：模型消息按流顺序产出、工具结果按插入顺序产出、UI 按 checkpoint 把已完成片段从动态区提交到静态区。
- [x] (2026-06-17 00:00+08:00) 已确认 OneCode 当前 provider 抽象不暴露 Anthropic content block 级别的 `text -> tool_use -> text` 块序列；本计划目标限定为“assistant message 完成 -> 工具结果按声明顺序完成 -> 下一轮 assistant 继续”的正确时序。
- [x] (2026-06-17 00:00+08:00) 已创建本 ExecPlan 初稿。
- [x] (2026-06-18 00:00+08:00) 已根据绑定机制审查结果修订计划：`assistant_call_id` 和 `model_turn_index` 从可选补充改为强制设计要求，所有 assistant/tool/checkpoint 事件必须携带稳定归属。
- [x] (2026-06-18 00:30+08:00) Milestone 1 完成：在 `core/stream_events.py` 添加 `mint_assistant_call_id` 和 `event_requires_attribution`；`core/loop.py` 每次进入模型调用时分配 `model_turn_index` 和 `assistant_call_id`，并把它们注入到所有 assistant/tool event metadata；`ui/cli/terminal/stream_state.py` 重写为 `StaticCommit` 队列 + `current_assistant_call_id` / `current_model_turn_index` / `tool_call_to_assistant_call_id` / `tool_call_declared_index` / `completed_tool_results_by_assistant` 字段。
- [x] (2026-06-18 00:45+08:00) Milestone 2 完成：`ui/cli/terminal/stream_reducer.py` 改写为强制消费稳定 ID（`_require_attribution`），并用 `release_ready_tool_result_commits` / `queue_assistant_checkpoint` helper 实现按声明顺序释放工具结果和 assistant checkpoint。`assistant_message_completed` 立即 emit checkpoint 并清空 `streaming_text`。
- [x] (2026-06-18 00:55+08:00) Milestone 3 完成：`ui/cli/terminal/output_coordinator.py::TerminalOutputCoordinator` 提供 `queue_commit(commit, *, workspace=None)` / `flush_ready_checkpoints()` API；`flush_static_commits` 仅作为向后兼容 alias 保留。Coordinator 内部按 `(assistant_call_id, sequence)` 去重。
- [x] (2026-06-18 01:00+08:00) Milestone 4 完成：`ui/cli/terminal/stream_session.py::StreamingSession` 改为事件驱动 checkpoint 循环 —— 每次 reducer 产生 ready commit 后立即 flush + invalidate 动态 app；`assistant_message_completed` 之后不再提前结束 dynamic app，要等 `completed` 或 `error`。`streaming_coalescer.py` 同步把 attribution metadata 透传到合成的 `assistant_delta` / `tool_progress` / `tool_call_delta`。
- [x] (2026-06-18 01:10+08:00) Milestone 5 完成：新测试 `tests/test_loop_assistant_call_ids.py` (4 passed) / `tests/test_cli_checkpoint_state.py` (13 passed)；`tests/test_cli_stream_reducer.py` (23 passed) / `tests/test_cli_output_coordinator.py` (12 passed) / `tests/test_cli_streaming_session_commit.py` (10 passed) 全部重写以反映新事件协议；`tests/test_cli_terminal.py` / `tests/test_cli_stream_view.py` 的 helper 加上默认 attribution 注入。
- [x] (2026-06-18 01:20+08:00) Milestone 6 完成：`docs/design-docs/cli-message-rendering-architecture.md` 和 `docs/design-docs/cli-architecture.md` 明确"checkpoint 提交模型"为事实来源；`assistant_call_id` / `model_turn_index` 标为强制稳定 ID。最终聚焦命令：compileall 0 错误，聚焦回归测试 154 通过、0 失败（`tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt` 和 `tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts` 是 pre-existing 失败与本计划无关，记录到 Surprises）。

## Surprises & Discoveries

- Observation: OneCode 当前 `core/loop.py` 已经实时转发 provider 事件，不是工具结果延迟进入历史区的主要原因。
  Evidence: `core/loop.py` 把 `content_delta` 立即转成 `assistant_delta`，把 `tool_call_completed` 立即转成 `tool_call_ready`，工具执行期间继续 yield `tool_started`、`tool_progress`、`tool_result`。

- Observation: 当前延迟来自 CLI 输出协调策略，而不是工具执行器必须延迟。
  Evidence: `ui/cli/terminal/output_coordinator.py::queue_tool_result()` 只 append 队列，不写 stdout；`flush_static_commits()` 才调用 `print_tool_result()`。`ui/cli/terminal/stream_session.py::run()` 在动态 app 退出后统一调用 `flush_static_commits()`。

- Observation: 用户提供的参考机制依赖 content block stop 作为原子边界，但 OneCode 当前 provider-neutral 事件只有 `content_delta`、`tool_call_delta`、`tool_call_completed`、`message_completed`，没有 provider block 级消息。
  Evidence: `services/model/stream.py::ModelStreamEventType` 不包含 `content_block_start` 或 `content_block_stop` 事件。

- Observation: OneCode 当前有 `tool_call.id -> tool_result.tool_call_id` 的协议级配对，也有 transcript 恢复时的孤立/缺失 tool result 修复，但没有类似 `sourceToolAssistantUUID` 的 UI 回链。
  Evidence: `services/tools/types.py::ToolCall.id` 与 `ToolExecutionResult.tool_call_id` 对应；`services/context/recovery.py::_sanitize_chain()` 会插入 interrupted tool result 或丢弃 orphan tool result；`rg "sourceToolAssistantUUID|assistant_call_id|model_turn_index"` 在现有生产代码中没有对应 UI 归属字段。

- Observation: `StreamingCoalescer.flush()` 合成 `assistant_delta` / `tool_progress` / `tool_call_delta` 时**丢弃**了原始事件 metadata，reducer 看到空 metadata 后会切到 error 状态。
  Evidence: `ui/cli/terminal/streaming_coalescer.py` 旧实现 `AgentEvent(type="assistant_delta", text=...)` 不带 metadata；reducer 收到后 `state.stream_mode == "error"`。修复方法：coalescer 在合并期间保留每个 pending 类型的 attribution metadata，flush 时把它合回到新事件。

- Observation: `StreamingSession._feed` 的 finally 块调 `_safe_exit(app)`，让 `app.run_async()` 在 reducer 还在收尾时退出；这并不会丢失 commit，但会触发 `session.run` 的最后兜底 commit+flush。
  Evidence: 测试 `test_streaming_session_drains_and_commits` 在三事件 (2 delta + 1 completed) 序列下，最终 captured_console 出现 committed text。

- Observation: Pre-existing 测试失败与本重构无关。
  Evidence: `tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt` 期望 `prompt` 含 "Tree-sitter"，但当前 `tools/bash/runner.py` 的 prompt 描述已被改写（"AST-based classification"），不包含 "Tree-sitter"。`tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts` 期望 prompt 以工具名开头，但当前 prompt 模板以 "Purpose:" 开头。两个失败属于工具 prompt 模板 refactor 遗留的 baseline 失败，不在本次 ExecPlan 范围内。

## Decision Log

- Decision: 本计划只实现 OneCode 当前抽象下的正确目标：assistant message 完成后静态化，该 message 声明的工具结果按声明顺序静态化，然后下一轮 assistant 继续流式显示。
  Rationale: 当前 provider adapter 不暴露 Anthropic block 级事件，强行在 CLI 层模拟 block 顺序会让 UI 依赖 provider 私有结构，违反 CLI 只消费 `AgentEvent` 的边界。
  Date/Author: 2026-06-17 / Codex。

- Decision: 这是替换式重构，不保留旧的“所有工具结果和 assistant final markdown 等 turn 结束后一把 flush”的兼容路径。
  Rationale: 旧路径本身就是用户可见问题来源。保留双路径会让输出时序继续分叉，测试也无法证明 CLI 只有一种正确提交模型。
  Date/Author: 2026-06-17 / Codex。

- Decision: 静态区写入仍只能经过 `TerminalOutputCoordinator`，不能让 reducer、view 或 `StreamingSession` 直接调用 `print_tool_result()` / `print_assistant_markdown()`。
  Rationale: 直接写 stdout 会重新引入 Rich 静态输出与 prompt_toolkit 动态区擦除的竞态。正确做法是增强 coordinator 的安全 checkpoint 提交能力，而不是绕过它。
  Date/Author: 2026-06-17 / Codex。

- Decision: 工具结果提交顺序以模型声明工具的顺序为准，而不是以工具实际完成时间为准。
  Rationale: 并发安全工具可能后声明先完成。如果按完成时间写入历史，用户看到的工具结果顺序会和 assistant 的工具调用声明顺序不一致，破坏可解释性。
  Date/Author: 2026-06-17 / Codex。

- Decision: 本重构必须引入稳定的 provider-neutral assistant 调用归属 ID，至少包含 `model_turn_index`，并优先同时提供 `assistant_call_id`。
  Rationale: 仅靠事件到达顺序能跑通简单场景，但 checkpoint 渲染需要明确知道 assistant markdown、tool_call_ready、tool_started、tool_result 分别属于哪一次模型调用。没有稳定 ID，UI 无法可靠表达“这个工具结果归属于哪段 assistant checkpoint”，后续恢复、并发和测试也会继续依赖隐含顺序。
  Date/Author: 2026-06-18 / Codex。

- Decision: Reducer 在 `tool_result` 时把 `assistant_committed` 留给 `assistant_message_completed` 处理；`completed` 收尾时只在 `streaming_text` 非空且 `assistant_committed` 为 False 时补一次 commit，避免重复 emit 同一 assistant message。
  Rationale: Provider 走标准路径会先发 `assistant_message_completed` 然后 `completed`，新协议下两条都会到达，reducer 必须保证 commit 不会重复。``assistant_committed`` 标志是 checkpoint 提交的去重证据。
  Date/Author: 2026-06-18 / Codex。

- Decision: `StreamingCoalescer` 在合并高频事件时保留每个 pending 类型的 attribution metadata，flush 时把它合回合成事件。
  Rationale: Reducer 要求所有 assistant/tool event 携带稳定 id；合并后丢失 metadata 会让 reducer 进入 error 状态。修复后 coalescer 仍能合并 deltas，但不会切断 attribution 链。
  Date/Author: 2026-06-18 / Codex。

- Decision: `flush_static_commits` 仅作为向后兼容 alias 保留（实现就是调 `flush_ready_checkpoints`），新代码不再使用。
  Rationale: 旧名暗示"等 turn 结束统一提交"，这正是被修复的 bug。保留 alias 是为了不破坏还在 import 的旧代码；文档明确指出新代码应使用 `flush_ready_checkpoints`。
  Date/Author: 2026-06-18 / Codex。

## Outcomes & Retrospective

实现已完成 6 个 milestone。最终结果：

- **删除的旧路径**:
  - `TerminalOutputCoordinator` 中 `queue_tool_result(result, call_id=, workspace=)` 的 entry path(被 `queue_commit` 取代)。
  - `StreamingSession.run()` 收尾时统一 `queue_assistant_markdown(self.state.streaming_text)` 的逻辑 —— assistant text 现在在 `assistant_message_completed` 事件到达时立即 commit 并清空。
  - 旧的 `state.pending_static_commits` 命名沿用,但内容扩展为 `StaticCommit` 统一队列(包含 `assistant_markdown` 和 `tool_result`),不再仅代表工具结果。
- **新增测试证明 checkpoint 时序**:
  - `tests/test_loop_assistant_call_ids.py::test_model_turn_events_share_assistant_call_id` — 同一次模型调用的所有 attributed 事件共享 `assistant_call_id`。
  - `tests/test_loop_assistant_call_ids.py::test_next_model_turn_gets_new_assistant_call_id` — 下一轮模型调用获得新 ID。
  - `tests/test_cli_checkpoint_state.py::test_assistant_checkpoint_clears_streaming_text` — checkpoint 提交后 `streaming_text` 被清空。
  - `tests/test_cli_checkpoint_state.py::test_static_commits_carry_assistant_call_id` — 所有 commit 携带稳定 ID。
  - `tests/test_cli_checkpoint_state.py::test_tool_results_are_released_in_declaration_order` — A 先声明、B 后声明, B 先完成时不被越过。
  - `tests/test_cli_checkpoint_state.py::test_tool_results_do_not_cross_assistant_call_id_boundaries` — 跨 `assistant_call_id` 不会串味。
  - `tests/test_cli_stream_reducer.py` 全套覆盖纯 reducer 行为(assistant delta 累积、tool 生命周期、声明顺序、缺 attribution 进入 error)。
  - `tests/test_cli_output_coordinator.py::test_checkpoint_flush_writes_immediately_after_queue` — checkpoint flush 后立即可见,不再等 turn end。
  - `tests/test_cli_streaming_session_commit.py::test_session_commits_assistant_tool_then_next_assistant_in_order` — 端到端顺序: first assistant → tool result → next assistant。
- **手动验收观察(代码层断言)**:
  - 测试断言 `state.streaming_text == ""` 在 `assistant_message_completed` 之后,且已 commit 的内容存在于 `pending_static_commits` 中。
  - 测试断言静态区 captured console 顺序为 first assistant markdown → tool result → second assistant markdown,与模型声明顺序一致。
  - 动态区"持续显示未提交 assistant 文本 + 活跃工具"的语义由 `CliStreamUiState.streaming_text` 与 `state.tools` 协同维持, view 只读 state, 所以测试不需要 prompt_toolkit 即可断言该事实。
- **Provider block 级事件的后续需求**: 仍没有动。OneCode 当前 provider-neutral 抽象不暴露 `content_block_start` / `content_block_stop`,所以无法像参考实现那样在 block 边界原子产出部分文本 + 部分工具调用。本次重构通过把"assistant message 完成 → 工具结果按声明顺序完成 → 下一轮 assistant 继续"作为 checkpoint 提交模型,在不引入 provider-specific 字段的前提下达到了等价的"按时间序观察 message 流"的视觉效果。

## Context and Orientation

OneCode 是一个 Python code agent runtime。`core/loop.py::AgentLoop` 是主循环，负责调用模型、转发流事件、执行工具、把工具结果写回 `MessageStore`。CLI 位于 `ui/cli/`，只是 UI 层，不应该理解 provider 私有协议，也不应该执行工具。

本计划使用几个术语。静态区指普通终端 scrollback，内容一旦打印就可以向上滚动查看，当前由 `ui/cli/terminal/static_output.py` 使用 Rich 输出。动态区指 `prompt_toolkit.Application(full_screen=False, erase_when_done=True)` 管理的可擦除区域，当前由 `ui/cli/terminal/stream_session.py` 渲染 live Markdown 和工具状态。checkpoint 指 UI 可以把一段已经完成的动态内容提交到静态区的边界，例如 assistant message 完成、某个按顺序可见的 tool result 完成。提交指把内容从动态状态队列写入静态区，并从动态区移除，后续动态区在它下面继续显示。

当前 TTY 路径是：`ui/cli/terminal/repl.py::InlineRepl._run_turn()` 创建 `StreamingSession`，把 `runtime.loop.stream()` 的 `AgentEvent` 交给它。`StreamingSession` 使用 `StreamingCoalescer` 合并高频事件，用 `stream_reducer.reduce_stream_event()` 修改 `CliStreamUiState`，用 `stream_view.render_stream_body_ansi()` 和 `render_status_fragments()` 画动态区。已完成工具结果由 reducer append 到 `state.pending_static_commits`，再由 `_commit_pending_to_coordinator()` 交给 `TerminalOutputCoordinator.queue_tool_result()`。当前 coordinator 只在 `StreamingSession.run()` 结束时一次性 `flush_static_commits()`，这就是工具结果延迟进入历史区的根源。

工具执行顺序当前在 `services/tools/executor.py::RegistryToolExecutor.execute()` 中已经有一层保守安排：非并发候选工具串行执行；连续的并发安全候选工具可以批量并发执行；并发批次最终按 `prepared` 列表顺序 yield result。这对 runtime 上下文是正确的，但 UI 仍需要自己的 checkpoint 提交模型，以便工具结果一旦按序可提交就进入静态区，而不是等整个 turn 结束。

本计划可以参考已存在的 `docs/exec-plans/active/cli-stream-rendering-state-refactor.md`，但不能依赖它作为唯一上下文。该旧计划已经把旧的混合渲染路径拆成 `stream_state`、`stream_reducer`、`stream_view` 和 `output_coordinator`。本计划在这个基础上继续替换“turn-end-only flush”的剩余问题。

## Plan of Work

第一阶段建立稳定 assistant 调用归属 ID 和 checkpoint 状态模型。先修改 `core/loop.py` 和 `core/stream_events.py` 的事件 metadata 约定：每次模型调用开始时生成一个递增的 `model_turn_index`，并派生一个本轮唯一的 `assistant_call_id`，例如由 session id、turn count 和 model turn index 组成的短字符串。`assistant_delta`、`tool_call_delta`、`tool_call_ready`、`assistant_message_completed`、`tool_started`、`tool_progress`、`tool_result`、`transition` 中凡是归属于某次模型调用或其工具执行的事件，都必须带上这两个字段。然后修改 `ui/cli/terminal/stream_state.py`，把当前 `pending_static_commits` 从“工具结果列表”升级为“静态提交队列”。提交队列里至少需要表达两类提交：assistant markdown commit 和 tool result commit。每个 commit 要有稳定的 sequence number、`model_turn_index`、`assistant_call_id`、提交类型、payload、committed 标志。`CliStreamUiState.streaming_text` 不再代表整个 turn 的最终 assistant 文本，而代表“当前尚未提交到静态区的 assistant 文本”。当 assistant message checkpoint 被提交后，`streaming_text` 必须清空，让下一轮 assistant 文本从空动态区继续。

第二阶段重写 reducer 的 checkpoint 语义。修改 `ui/cli/terminal/stream_reducer.py::reduce_stream_event()`。`assistant_delta` 继续追加到 `streaming_text`，但必须把当前动态文本归入事件 metadata 中的 `assistant_call_id`。如果 reducer 收到缺少稳定 ID 的 assistant 或工具事件，应进入 error state 或记录测试可断言的 fallback error，而不是静默依赖上一条事件。`assistant_message_completed` 不应只设置布尔标志，而应创建一个 assistant markdown commit，commit 文本来自当前 `streaming_text` 或 event text，并携带同一个 `assistant_call_id`，然后把 UI 状态切到工具阶段或等待模型阶段。`tool_call_ready` 记录工具声明顺序，给每个 tool call 一个 `declared_index`，并保存 `tool_call_id -> assistant_call_id` 的映射。`tool_result` 不再直接等 turn 结束提交，而是通过 `tool_call_id` 找到对应 `assistant_call_id` 和 `declared_index`，进入一个按 `assistant_call_id + declared_index` 管理的 completed bucket。reducer 或独立 helper 要只释放“同一 assistant_call_id 下从最小未提交 index 开始连续完成”的 tool result commits，防止后声明但先完成的工具越过前面的工具。`completed` 事件只表示整个 turn 结束，不再承担“把所有历史统一写出”的职责。

第三阶段增强输出协调器。修改 `ui/cli/terminal/output_coordinator.py::TerminalOutputCoordinator`，用 checkpoint API 替代 turn-end-only API。保留“只有 coordinator 能写静态区”的边界，但删除或重命名让语义误导的 `flush_static_commits()`，改为 `flush_ready_checkpoints()` 或 `commit_ready_static_output()`。该方法必须能在动态 app 仍运行时安全提交：优先通过 prompt_toolkit 的 `run_in_terminal` 或等价的 terminal-safe 回调短暂停止动态绘制，写入 Rich 静态输出后再恢复动态绘制。如果实现选择退出并重建动态 app，也必须封装在 `StreamingSession` 和 coordinator 内，外部调用者不能感知双路径。coordinator 的 pending commit 去重应使用 commit sequence 和 `assistant_call_id`，不要只按文本内容或 tool name 判断重复。

第四阶段重写 `StreamingSession` 运行循环。修改 `ui/cli/terminal/stream_session.py::StreamingSession.run()` 和 `_feed()`。旧行为是动态 app 运行到 preview complete，最后 flush。新行为是每次 reducer 产生 ready checkpoint 后，`StreamingSession` 请求 coordinator 提交 checkpoint，并让动态区重绘到新的位置。动态区应始终显示当前未提交 assistant 文本和当前活跃工具，不显示已经静态化的 assistant 文本或工具结果。`_should_complete_preview()` 不能在 `assistant_message_completed` 后因为没有 active tools 就直接结束整个 dynamic app；如果 loop 后续可能进入下一轮模型调用，动态 session 应继续运行，直到真正 `completed` 或 `error`。最终 `completed` 到达时，只 flush 尚未提交的 checkpoint，不重复打印已提交 assistant 文本或工具结果。

第五阶段补充稳定 ID 和 UI 绑定测试。新增或修改测试，证明 `AgentLoop` 对同一次模型调用发出的 `assistant_delta`、`tool_call_ready`、`assistant_message_completed` 带有相同 `assistant_call_id`，并且该调用产生的 `tool_started`、`tool_progress`、`tool_result` 也带有同一个归属 ID。还要证明下一轮模型调用使用新的 `assistant_call_id`。这些字段只用于 UI 分组和 trace，不改变 message store、工具执行或 provider adapter。不要引入 provider-specific content block 字段。

第六阶段删除旧路径和更新文档。删除 `TerminalOutputCoordinator` 中只服务 turn-end flush 的内部队列结构和测试。删除 `StreamingSession.run()` 结束时统一 `queue_assistant_markdown(self.state.streaming_text)` 的逻辑。更新 `docs/design-docs/cli-message-rendering-architecture.md` 和必要的 `docs/design-docs/cli-architecture.md`，明确当前主屏是 checkpoint 渲染，不再是 turn-end commit，并明确 `assistant_call_id` / `model_turn_index` 是 CLI checkpoint 归属的稳定事实来源。旧文档中“工具结果在 turn 结束时写静态区”的描述必须全部改掉。

## Concrete Steps

从 `D:\study\OneCode` 开始。先确认工作树状态，避免覆盖他人改动：

    git status --short

阅读当前相关文件：

    Get-Content ui\cli\terminal\stream_state.py
    Get-Content ui\cli\terminal\stream_reducer.py
    Get-Content ui\cli\terminal\stream_session.py
    Get-Content ui\cli\terminal\output_coordinator.py
    Get-Content tests\test_cli_output_coordinator.py
    Get-Content tests\test_cli_streaming_session_commit.py

Milestone 1 修改 `core/loop.py`、`core/stream_events.py` 和 `ui/cli/terminal/stream_state.py`，并新增或重写 `tests/test_loop_assistant_call_ids.py` 与 `tests/test_cli_checkpoint_state.py`。测试应构造 loop 事件序列，断言同一次模型调用的 assistant/tool 事件共享 `assistant_call_id` 和 `model_turn_index`，下一轮模型调用使用新的 ID；还应构造 state，模拟 assistant commit、tool declaration、tool result completion，断言 commit sequence、稳定归属 ID 和未提交文本清空行为。运行：

    uv run python -m pytest tests/test_loop_assistant_call_ids.py tests/test_cli_checkpoint_state.py tests/test_cli_stream_reducer.py -q

Milestone 2 修改 `ui/cli/terminal/stream_reducer.py`。测试应覆盖：缺少 `assistant_call_id` 的 assistant/tool 事件会进入可诊断错误；`assistant_message_completed` 产生带 `assistant_call_id` 的 assistant checkpoint；两个工具按 A、B 声明但 B 先完成时不会先提交 B；A 完成后 A 和 B 才按 A、B 顺序释放；不同 `assistant_call_id` 下的工具结果不会互相释放。运行：

    uv run python -m pytest tests/test_cli_stream_reducer.py tests/test_cli_checkpoint_state.py -q

Milestone 3 修改 `ui/cli/terminal/output_coordinator.py` 和对应测试。旧的 `flush_static_commits()` 测试应被删除或改写为 checkpoint flush 测试，不能保留旧 API 的兼容测试。测试使用 captured Rich console，断言 queue 方法不写 stdout，checkpoint flush 后立即写出 assistant markdown 或 tool result，并且重复 flush 不重复输出。运行：

    uv run python -m pytest tests/test_cli_output_coordinator.py -q

Milestone 4 修改 `ui/cli/terminal/stream_session.py` 和集成测试。测试应模拟事件序列：assistant delta、assistant completed、tool ready、tool started、tool result、assistant delta、completed。预期静态输出顺序是 first assistant markdown、tool result、final assistant markdown；动态 state 在 first assistant commit 后不再包含 first assistant 文本。运行：

    uv run python -m pytest tests/test_cli_streaming_session_commit.py tests/test_cli_terminal.py tests/test_streaming_coalescer.py -q

Milestone 5 补充稳定 ID 绑定和端到端顺序测试。测试应覆盖 `AgentLoop` 到 `StreamingSession` 的事件流：第一轮 assistant 和工具共享同一个 `assistant_call_id`；工具结果静态提交时带有该 ID；第二轮 assistant 使用新 ID 并在工具结果之后继续输出。运行：

    uv run python -m pytest tests/test_loop_assistant_call_ids.py tests/test_cli_streaming_session_commit.py tests/test_cli_terminal.py -q

Milestone 6 搜索并删除旧路径引用。以下搜索不应在生产代码或新测试中出现旧语义引用：

    rg "turn end|turn-end|flush_static_commits|pending_static_commits" ui tests docs

如果保留了 `flush_static_commits` 名称作为 checkpoint flush 的实现细节，必须在文档和测试中明确它不再表示 turn-end-only；更推荐删除或重命名，避免误导。最后运行聚焦回归：

    uv run python -m compileall ui services core
    uv run python -m pytest tests/test_loop_assistant_call_ids.py tests/test_cli_checkpoint_state.py tests/test_cli_stream_reducer.py tests/test_cli_stream_view.py tests/test_cli_output_coordinator.py tests/test_cli_streaming_session_commit.py tests/test_cli_terminal.py tests/test_streaming_coalescer.py tests/test_loop_realtime_streaming.py tests/test_cli_tool_renderers.py -q

手动验收：

    uv run python -m ui.cli.app

输入一个会触发工具的请求，例如：

    请搜索仓库里 CLI streaming 的实现，并读取相关文件

观察结果应是：第一段 assistant 文本完成后进入 scrollback；工具状态继续在底部动态区显示；工具结果完成后立即进入 scrollback；随后下一轮 assistant 文本在工具结果下面继续流式显示。整个过程中，静态输出不能插进动态区内部，动态区不能残留已经静态化的历史文本。

## Validation and Acceptance

自动化验收必须证明四件事。第一，绑定正确：同一次模型调用的 assistant delta、tool call ready、assistant message completed、tool started、tool progress 和 tool result 共享稳定 `assistant_call_id` 与 `model_turn_index`；下一次模型调用使用新的 ID。第二，事件顺序正确：assistant completed 会产生 assistant checkpoint，tool result 会按 tool declaration 顺序产生 checkpoint，`completed` 只做收尾不重复提交。第三，终端提交正确：checkpoint flush 期间队列写入静态区，重复 flush 不重复输出，queue 阶段不写 stdout。第四，动态区正确：已 checkpoint 的 assistant 文本和工具结果从动态 state 中移除，后续动态渲染只显示未提交内容和活跃工具。

新增或重写的测试至少包括：

- `tests/test_loop_assistant_call_ids.py::test_model_turn_events_share_assistant_call_id`
- `tests/test_loop_assistant_call_ids.py::test_next_model_turn_gets_new_assistant_call_id`
- `tests/test_cli_checkpoint_state.py::test_assistant_checkpoint_clears_streaming_text`
- `tests/test_cli_checkpoint_state.py::test_static_commits_carry_assistant_call_id`
- `tests/test_cli_stream_reducer.py::test_tool_results_are_released_in_declaration_order`
- `tests/test_cli_stream_reducer.py::test_tool_results_do_not_cross_assistant_call_id_boundaries`
- `tests/test_cli_output_coordinator.py::test_checkpoint_flush_writes_immediately_after_queue`
- `tests/test_cli_streaming_session_commit.py::test_session_commits_assistant_tool_then_next_assistant_in_order`
- `tests/test_cli_terminal.py` 中覆盖 `StreamingSession` 与 prompt_toolkit dummy input/output 的集成场景

最终聚焦命令：

    uv run python -m compileall ui services core
    uv run python -m pytest tests/test_loop_assistant_call_ids.py tests/test_cli_checkpoint_state.py tests/test_cli_stream_reducer.py tests/test_cli_stream_view.py tests/test_cli_output_coordinator.py tests/test_cli_streaming_session_commit.py tests/test_cli_terminal.py tests/test_streaming_coalescer.py tests/test_loop_realtime_streaming.py tests/test_cli_tool_renderers.py -q

通过标准是 compileall 无错误，pytest 全部通过。若仓库存在与本计划无关的既有失败，必须在 `Surprises & Discoveries` 中记录失败测试名、失败原因和基线证据，不能把它混入本重构结果。

手动验收通过标准是用户肉眼能看到 checkpoint 行为：工具结果不再等最终 assistant 文本结束才出现；历史区顺序与 assistant 声明工具的顺序一致；动态区始终位于最新静态输出下面，没有文本重叠、残留或融合。

## Idempotence and Recovery

本计划是替换式重构。实现过程中可以分阶段添加新类型和测试，但 Milestone 5 完成时不能保留旧的 turn-end-only flush 生产路径，也不能保留只为旧 API 服务的兼容测试。如果中途失败，先运行 `git status --short` 查看改动范围，再从最近通过的 milestone 测试恢复。不要使用 `git reset --hard` 或 `git checkout --` 回滚，因为工作树可能包含用户或其他 agent 的改动。

如果 checkpoint flush 在真实终端中出现动态区撕裂，不要绕过 coordinator 直接打印。应在 `TerminalOutputCoordinator` 内调整 terminal-safe 写入策略，例如使用 prompt_toolkit 的安全终端写入机制，或让 `StreamingSession` 在 checkpoint 时短暂退出并重建动态 app。无论采用哪种策略，外部 API 仍应保持“queue checkpoint -> coordinator 安全提交”的单一路径。

`AgentEvent.metadata` 中的 `model_turn_index` 和 `assistant_call_id` 是本计划的必需字段，不是 fallback。实现者不要把 Anthropic、OpenAI 或任何具体 provider 的 wire 字段传进 CLI；稳定 ID 应由 OneCode runtime 自己生成。

## Artifacts and Notes

用户提供的参考机制可概括为：底层 SSE 保证流事件顺序；content block stop 是模型消息的原子产出边界；query 主循环先产出模型消息，再注册工具执行，再立即产出按顺序可见的工具结果；工具执行器用插入顺序数组保证并发工具的结果不乱序。OneCode 当前不能直接复制 content block stop，因为 provider-neutral 事件没有 block 级概念，但可以复制“模型消息 checkpoint、工具声明顺序、UI checkpoint 提交”这三个原则。

参考文件索引如下。实现者应把这些文件作为机制参考，而不是逐行搬运 TypeScript/React 实现。

- `docs/references/主循环和重建上下文/query.ts`。参考机制是主查询循环如何先 yield 模型消息，再把 assistant tool_use 注册到工具执行器，然后立即 drain 已完成工具结果。快速定位搜索：`streamingToolExecutor.addTool`、`getCompletedResults`、`normalizeMessagesForAPI`、`sourceToolAssistantUUID`。

- `docs/references/Tools_full/services/tools/StreamingToolExecutor.ts`。参考机制是工具按 addTool 插入顺序保存，执行可以并发，但 `getCompletedResults()` 按插入顺序产出结果；前序非并发安全工具未完成时阻止后序结果越过。快速定位搜索：`export class StreamingToolExecutor`、`Results are buffered and emitted in the order tools were received`、`*getCompletedResults()`、`getRemainingResults()`、`pendingProgress`。

- `docs/references/Tools_full/services/tools/toolExecution.ts`。参考机制是工具结果消息如何同时携带 API 配对字段 `tool_use_id` 和 UI 回链字段 `sourceToolAssistantUUID`。本计划不复制字段名，但借鉴“协议配对 ID + UI 归属 ID”双绑定思想。快速定位搜索：`sourceToolAssistantUUID`、`tool_use_id`、`createUserMessage`。

- `docs/references/ui/utils/messages.ts`。参考机制有三类：`handleMessageFromStream()` 把流事件分发到 UI streaming state；`buildMessageLookups()` 建立 tool_use/tool_result/progress 的 O(1) 查找表；`ensureToolResultPairing()` 在 API 调用前修复缺失、孤立或重复的 tool_result 配对。快速定位搜索：`handleMessageFromStream`、`buildMessageLookups`、`ensureToolResultPairing`、`normalizeMessagesForAPI`、`content_block_stop`。

- `docs/references/ui/utils/sessionStorage.ts`。参考机制是 JSONL/session 存储如何使用 `sourceToolAssistantUUID` 把 tool_result 记录挂回产生它的 assistant message，并说明 streamed response 在 `content_block_stop` 时会拆成多条 assistant message。快速定位搜索：`sourceToolAssistantUUID`、`content_block_stop`、`normalizeMessagesForAPI's merge`。

- `docs/references/ui/screens/REPL.tsx`。参考机制是 REPL 层如何把流事件交给 `handleMessageFromStream()`，并分别维护 streaming text、streaming tool uses、stream mode 等 UI 状态。快速定位搜索：`handleMessageFromStream(event`、`streamingToolUses`、`setStreamMode`、`onQueryEvent`。

- `docs/references/ui/components/messages/AssistantToolUseMessage.tsx`。参考机制是工具调用在 UI 中如何区分 queued、in-progress、resolved 等状态。本计划只借鉴状态分层，不引入 React/Ink。快速定位搜索：`inProgressToolUseIDs`、`isQueued`、`isResolved`。

- `C:\Users\rowla\.codex\attachments\f35be17d-c7c6-4cfe-a8d3-4824ed001fb0\pasted-text.txt`。这是用户提供的三层 ID 绑定说明，解释 `message.id`、`tool_use.id`、`assistantMessage.uuid`、`sourceToolAssistantUUID` 的分工。本计划已据此把 `assistant_call_id` / `model_turn_index` 改为强制要求。快速定位搜索：`三层 ID 体系`、`sourceToolAssistantUUID`、`ensureToolResultPairing`。

- `C:\Users\rowla\.codex\attachments\1113d473-feff-4f16-839a-ab4a4a5eb175\pasted-text.txt`。这是用户提供的流式时序说明，解释 SSE 顺序、`content_block_stop` 原子边界、query 循环交错 yield、StreamingToolExecutor 插入顺序产出。本计划已据此采用 checkpoint 提交和工具声明顺序产出。快速定位搜索：`content_block_stop`、`getCompletedResults`、`Insertion-Order Yield`、`总结：时序保证架构图`。

当前 OneCode 相关路径：

    core/loop.py
    core/stream_events.py
    services/model/stream.py
    services/tools/executor.py
    ui/cli/terminal/stream_state.py
    ui/cli/terminal/stream_reducer.py
    ui/cli/terminal/stream_view.py
    ui/cli/terminal/stream_session.py
    ui/cli/terminal/output_coordinator.py
    ui/cli/terminal/static_output.py

需要删除或替换的旧语义包括：

    TerminalOutputCoordinator.flush_static_commits as turn-end-only flush
    StreamingSession.run final-only assistant markdown queue
    state.pending_static_commits as only tool-result commits
    tests that assert tool results are printed only after the dynamic app exits

## Interfaces and Dependencies

不新增第三方依赖。继续使用标准库、Rich 和 prompt_toolkit。新增或修改接口主要集中在 `ui/cli/terminal/`，但 core 层必须增加 provider-neutral metadata，用于稳定绑定 assistant message 与工具事件。

在 `core/loop.py` 中，每次进入模型调用前创建：

    model_turn_index: int
    assistant_call_id: str

`model_turn_index` 是当前 interaction 内递增的整数，从 1 开始即可。`assistant_call_id` 是当前 runtime session 内稳定唯一的字符串，可以由 `state.session_id`、`state.turn_count` 和 `model_turn_index` 组合生成。不要使用 provider message id，因为 OneCode 当前 provider-neutral 抽象不保证所有 provider 都有 message id。

在 `core/stream_events.py` 的 `AgentEvent.metadata` 约定中，以下事件必须携带 `model_turn_index` 和 `assistant_call_id`：

    assistant_delta
    tool_call_delta
    tool_call_ready
    assistant_message_completed
    tool_started
    tool_progress
    tool_result

`transition` 事件如果发生在某个模型调用内部，也应携带这两个字段；如果是 max turns 或整体 completed 这类不归属于某次模型调用的 transition，可以只携带当前可用的 `model_turn_index` 或不携带，但测试必须覆盖主要 assistant/tool 事件。

在 `ui/cli/terminal/stream_state.py` 中定义或调整这些概念：

    StaticCommit
        sequence: int
        kind: "assistant_markdown" | "tool_result" | "status"
        payload: object
        model_turn_index: int
        assistant_call_id: str
        tool_declared_index: int | None
        committed: bool

    CliStreamUiState
        streaming_text: str
        current_assistant_call_id: str
        current_model_turn_index: int | None
        tools: dict[str, StreamingToolUseState]
        tool_call_to_assistant_call_id: dict[str, str]
        tool_call_declared_index: dict[str, int]
        ready_static_commits: list[StaticCommit]
        completed_tool_results_by_assistant: dict[str, dict[int, ToolExecutionResult]]
        next_tool_result_index_to_release_by_assistant: dict[str, int]
        stream_mode: str
        error_text: str
        assistant_completed: bool
        turn_completed: bool

具体字段名可以在实现时微调，但职责不能退回旧模型。`ready_static_commits` 必须同时承载 assistant 和 tool result checkpoint；每个 checkpoint 必须携带 `assistant_call_id`；工具结果必须有声明顺序 index；`streaming_text` 必须能在 assistant checkpoint 后清空。

在 `ui/cli/terminal/stream_reducer.py` 中继续保留：

    reduce_stream_event(state: CliStreamUiState, event: AgentEvent) -> None

它仍是纯 reducer，不写 stdout，不创建 Rich console，不退出 prompt_toolkit app。可以新增 helper：

    release_ready_tool_result_commits(state: CliStreamUiState, assistant_call_id: str) -> None
    queue_assistant_checkpoint(state: CliStreamUiState, text: str, metadata: dict) -> None

这些 helper 仍只能修改 state。

在 `ui/cli/terminal/output_coordinator.py` 中用 checkpoint API 取代旧 turn-end-only flush：

    class TerminalOutputCoordinator:
        def queue_commit(self, commit: StaticCommit) -> None: ...
        async def flush_ready_checkpoints(self, *, app: Application | None = None) -> None: ...
        def pending_commit_count(self) -> int: ...

如果为了测试保留同步版本，可以提供：

    def flush_ready_checkpoints_sync(self) -> None: ...

但生产路径应使用能在动态 app 运行时安全写终端的机制。无论名称如何，旧的 `flush_static_commits()` 不能继续表示“turn 结束统一提交”。

在 `ui/cli/terminal/stream_session.py` 中保持入口：

    class StreamingSession:
        async def run(self, events, *, input=None, output=None) -> CliStreamUiState: ...

内部流程必须改成事件驱动 checkpoint：每次 `_apply_event()` 后检查 state 中的 ready commits，交给 coordinator flush；flush 后 invalidate 动态 app；直到 `completed` 或 `error` 才结束 session。

在 `core/stream_events.py` 中扩展 `AgentEvent.metadata` 约定，不新增 provider-specific event type。必需字段：

    model_turn_index: int
    assistant_call_id: str

这两个字段只帮助 UI 分组，不改变 tool execution、message store 或 provider adapter 行为。所有主要 assistant/tool 事件缺少这些字段都应被视为实现错误，并由测试覆盖。

## Revision Note

2026-06-17 / Codex：创建本 ExecPlan，原因是用户要求参考 Claude 风格的消息与工具结果时序机制，为 OneCode 当前更正确的目标撰写中文计划。本文明确采用替换式重构，删除旧的 turn-end-only flush 路径，不为了迁移安全保留旧兼容分支。

2026-06-18 / Codex：将 `assistant_call_id` / `model_turn_index` 从可选补充改为强制设计要求，原因是 OneCode 当前只有 `tool_call_id` 配对和 transcript 恢复修复，缺少 UI 归属回链；checkpoint 渲染需要稳定 ID 才能可靠绑定 assistant 文本、工具声明和工具结果。
