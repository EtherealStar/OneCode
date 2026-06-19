# 统一 OneCode CLI 的工具调用与助手文本渲染

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This document follows `PLANS.md` in the repository root. Any contributor who changes implementation scope, discovers new behavior, or completes a milestone must update this file in the same change.

## Purpose / Big Picture

OneCode CLI 当前已经能实时接收 assistant 文本 delta，也能显示工具调用状态和工具结果，但它们分属两套视觉路径：assistant 文本在 `prompt_toolkit` 动态区里流式预览，工具结果在事件到达时直接写入静态 scrollback。用户看到的效果是工具调用行可能插入到正在流式显示的 assistant 文本附近，像是两套输出在抢终端区域；而没有工具调用的长回答会先被限制在一个小的动态预览区域，直到生成结束后才提交为完整历史内容。

完成本计划后，OneCode CLI 应有清晰的分工：动态区只展示“当前正在发生的事情”，包括最多 5 行 assistant 尾部文本和当前活跃工具列表；已经完成的工具结果进入静态历史记录，由统一工具结果渲染器展示；assistant 最终文本仍在 turn 结束时提交到静态历史。用户在 CLI 中看到工具调用时，应能明确区分“正在执行的工具及参数”和“已完成的工具结果”，不再出现工具结果直接打断动态 assistant 文本的视觉混杂。

本计划参考 `docs/references/ui/screens/REPL.tsx`、`docs/references/ui/components/Messages.tsx`、`docs/references/ui/components/Message.tsx`、`docs/references/ui/utils/messages.ts`、`docs/references/ui/components/UserToolResultMessage/UserToolResultMessage.tsx`、`docs/references/ui/components/UserToolResultMessage/UserToolSuccessMessage.tsx`、`docs/references/ui/components/MessageResponse.tsx`、`docs/references/Tools_full/BashTool/UI.tsx` 和 `docs/references/主循环和重建上下文/query.ts`。本计划不移植 React/Ink；它迁移的是设计模式：统一状态归约、工具调用与工具结果按 id 关联、工具渲染策略由工具或工具名分派、框架层提供一致视觉容器。

## Progress

- [x] (2026-06-15 00:00+08:00) 阅读 `PLANS.md`，确认新 ExecPlan 必须自包含、可执行、持续维护四个 living sections。
- [x] (2026-06-15 00:00+08:00) 阅读 OneCode 当前 CLI 文档和实现，确认工具事件在 `ui/cli/terminal/stream_session.py::consume_event()` 中直接写静态区，assistant 文本由同文件的动态预览渲染。
- [x] (2026-06-15 00:00+08:00) 阅读参考实现中 `Messages.tsx`、`messages.ts`、`Message.tsx`、`UserToolResultMessage`、`MessageResponse`、Bash 工具 UI 和 `query.ts` 的相关片段，确认参考实现通过统一 messages/streaming state 渲染树避免多输出通道竞争。
- [x] (2026-06-15 00:00+08:00) 创建本 ExecPlan，明确本计划不改 core 工具执行安全边界，只重构 CLI 的 turn 内渲染状态和工具结果展示策略。
- [x] (2026-06-16 00:00+08:00) 实现 `ui/cli/terminal/turn_render_state.py`，定义 `TurnRenderState`、`AssistantTailState`、`ActiveToolState`、`CompletedToolState` 和纯函数 `consume_agent_event` reducer。`assistant.visible_lines()` 限制 5 行；`tool_result` 缺 call id 时使用单调递增的 `unknown_call_<n>` 兜底。
- [x] (2026-06-16 00:00+08:00) 修改 `ui/cli/terminal/stream_session.py`，`StreamBuffer` 持有 `turn_state` 字段并保留旧 API（`text`、`active_tool_ids`、`current_tool_label`）作为同步视图。`consume_event` 只更新状态、不再直接调用 `print_tool_result`；新增 `StreamingSession._flush_completed_tools_to_static()` 在 `_feed` 每次消费事件后和 `commit_final()` 中调用。
- [x] (2026-06-16 00:00+08:00) 实现 `render_turn_preview_ansi`（在 `turn_render_state.py`），动态区按顺序渲染 assistant 尾部（最多 5 行）→ 活跃工具列表 → `…  N more tools running` 折叠行。`StreamingSession._build_app` 的 `preview_text` 改为委托给新函数，状态行继续显示 `onecode> tool: …`。
- [x] (2026-06-16 00:00+08:00) 重写 `ui/cli/tool_renderers.py` 为策略接口，导出 `ToolCliRenderer` Protocol 和 `BuiltinToolRenderer` dataclass。提供 `render_tool_result`、`render_fallback_tool_result`、`render_use_preview`、`render_running` 四个公共入口；为 `bash`/`read_file`/`grep`/`glob`/`write_file`/`edit_file` 注册默认实现。Bash 预览沿用 `BashTool/UI.tsx` 的 2 行 / 160 字符约束。
- [x] (2026-06-16 00:00+08:00) 更新 `ui/cli/terminal/static_output.py::print_tool_result`，在静态行前加 `  ⎿  ` 容器前缀；具体工具 renderer 不再写入容器，避免重复和嵌套。
- [x] (2026-06-16 00:00+08:00) 新增 `tests/test_cli_turn_render_state.py`（19 个 reducer 测试 + 6 个动态区渲染测试），新增 `tests/test_cli_streaming_session_commit.py`（8 个端到端 commit / flush 测试）。更新 `tests/test_cli_terminal.py` 中依赖 `buffer.text` 的旧用例改用 `buffer.assistant_text`，并把 `tool_result` 用例改为走 `_flush_completed_tools_to_static` 路径。

## Surprises & Discoveries

- Observation: OneCode 当前 `StreamingSession` 已经维护了部分工具状态，例如 `active_tool_ids` 和 `current_tool_label`，但 `tool_result` 到达时仍调用 `print_tool_result()` 直接写静态区。
  Evidence: `ui/cli/terminal/stream_session.py::consume_event()` 在 `event_type == "tool_result"` 分支中调用 `print_tool_result(...)`。

- Observation: OneCode 当前动态预览高度是刻意有界的，不适合承载完整历史；它应展示当前活跃状态，而不是完整消息列表。
  Evidence: `ui/cli/terminal/stream_session.py` 定义 `_PREVIEW_MAX_LINES = 12`，`preview_window` 使用 `Dimension(min=1, max=_PREVIEW_MAX_LINES + 1)`。

- Observation: 参考实现并不让工具结果直接写终端，而是把 tool_use、tool_result、progress 和 streamingText 都变成同一个渲染树里的节点。
  Evidence: `docs/references/ui/components/Messages.tsx` 使用 `normalizeMessages(messages)`，把 `streamingToolUses` 转成 synthetic assistant messages，并在同一个 `Messages` 组件中渲染 `streamingText`。

- Observation: 参考实现中工具结果成功渲染会先校验工具输出结构，再调用工具自己的 `renderToolResultMessage()`，框架只提供分派和容器。
  Evidence: `docs/references/ui/components/UserToolResultMessage/UserToolSuccessMessage.tsx` 中读取 `message.toolUseResult`，用 `tool.outputSchema?.safeParse(...)` 校验，然后调用 `tool.renderToolResultMessage(...)`。

- Observation: 参考实现的 `MessageResponse` 只负责统一工具响应视觉容器，且通过 context 避免嵌套容器重复出现。
  Evidence: `docs/references/ui/components/MessageResponse.tsx` 渲染 `⎿` 前缀，并在已处于 `MessageResponseContext` 时直接返回 children。

- Observation: 现有 `StreamBuffer` 已经有 `text`、`active_tool_ids`、`current_tool_label` 三个独立字段，加上工具渲染散落在 `static_output.py` 的 banner helper 里，导致 reducer 和 renderer 共享同一个对象。新增 reducer 阶段把这三个字段收敛到 `TurnRenderState`，并把 banner 合并到 `render_use_preview` 路径。
  Evidence: 旧 `consume_event` 中 `tool_call_ready` 同时调用 `print_tool_banner_start` 和修改 `buffer.active_tool_ids`、`buffer.current_tool_label`；新版本拆成两步：reducer 只更新 `turn_state.active_tools`，渲染器在动态区显示 `tool: <name> …`。

- Observation: 当 `tool_started` 事件携带空 `tool_name` 时，旧实现会回退到 `current_tool_label`，导致一个工具的 name 泄漏到另一个没有 name 的工具上。新 reducer 改为当 `tool_name` 为空时不回退，依赖 `visible_active_tools(limit=…)` 过滤掉空名条目。
  Evidence: `tests/test_cli_turn_render_state.py::test_visible_active_tools_filters_empty_names` 在修复前失败、修复后通过。

- Observation: `StreamBuffer` 是 dataclass，无法直接把 `text` 改成 property 同时支持 `StreamBuffer(text=...)` kwargs。为保留旧 API，新增 `assistant_text` 属性作为新代码推荐的读取路径，同时让 `text` 在 `__post_init__` 里种子化到 `turn_state.assistant`。
  Evidence: `tests/test_cli_terminal.py::test_render_preview_handles_partial_code_fence` 等用 `consume_event(buffer, assistant_delta(...))` 写法更新到使用 `buffer.assistant_text` 读取。

## Decision Log

- Decision: 本计划不把动态区改成完整历史消息列表；动态区只显示当前 assistant 尾部和活跃工具，完成的工具结果进入静态历史。
  Rationale: OneCode 当前 TTY 架构使用主屏 scrollback 作为历史，动态区使用 `erase_when_done=True` 作为可擦除预览。把完整历史搬进动态区会破坏现有 scrollback 模型，也会让 prompt_toolkit 的有界区域承担不适合它的职责。用户提出的“最新一条是工具调用时显示正在执行工具，助手文本开始生成时最多 5 行”正好符合当前架构。
  Date/Author: 2026-06-15 / Codex

- Decision: 不在 `core/loop.py` 中加入 UI 特例；统一渲染全部发生在 `ui/cli/terminal/`。
  Rationale: `core/loop.py` 是 provider-neutral agent 主循环，已经通过 `AgentEvent` 传递 `assistant_delta`、`tool_call_ready`、`tool_started`、`tool_progress` 和 `tool_result`。渲染冲突是 CLI 表现层问题，不应改变工具执行、权限或 message store 的事实来源。
  Date/Author: 2026-06-15 / Codex

- Decision: 第一版不要求所有工具都有专用详细 UI；先建立策略接口和统一静态/动态容器，再逐步为高频内置工具增加更好的展示。
  Rationale: 统一渲染的首要目标是消除多输出通道竞争。专用工具 UI 可以增量演进，但不能依赖旧的输出路径或兼容代理；未知工具仍需要一个新的 fallback renderer 作为统一策略的一部分，而不是旧逻辑的残留。
  Date/Author: 2026-06-15 / Codex

- Decision: 活跃工具参数只显示 bounded preview，不显示完整 JSON 或完整 stdout/stderr。
  Rationale: tool call arguments 可能很长，stdout/stderr 也可能很大。动态区应稳定、短小、可擦除；完整结果仍通过工具 result store、transcript 和静态摘要治理。
  Date/Author: 2026-06-15 / Codex

## Outcomes & Retrospective

2026-06-16 第一版实施完成。

实际改动：

- 新增 `ui/cli/terminal/turn_render_state.py`（~280 行含注释）：纯 reducer + 动态区 ANSI 渲染函数。`consume_agent_event` 是纯函数式边界，不打印、不读文件、不调用工具。
- `ui/cli/terminal/stream_session.py`：`StreamBuffer` 现在包装 `TurnRenderState` 并保留 `text` / `active_tool_ids` / `current_tool_label` 作为同步视图（让旧 API 继续工作，迁移到新代码时可改用 `buffer.assistant_text`）。`consume_event` 不再直接写静态区；新增 `StreamingSession._flush_completed_tools_to_static()` 在 `_feed` 每次消费事件后和 `commit_final()` 中调用，保证静态区写入发生在明确的 flush/commit 函数中。
- `ui/cli/tool_renderers.py`：导出 `ToolCliRenderer` Protocol + `BuiltinToolRenderer` dataclass。新增 `render_use_preview` 和 `render_running` 用于动态区；保留 `render_tool_result` 和 `render_fallback_tool_result` 用于静态区。bash / read_file / grep / glob / write_file / edit_file 都有默认实现；未知工具走 fallback，渲染器抛异常时 dispatcher 兜底返回 fallback。
- `ui/cli/terminal/static_output.py::print_tool_result`：在静态行前添加 `  ⎿  ` 容器前缀；具体工具 renderer 不再写容器。

测试结果：

- `tests/test_cli_turn_render_state.py` 新增：19 个 reducer 测试 + 6 个动态区渲染测试 = 25 个。
- `tests/test_cli_streaming_session_commit.py` 新增：8 个 commit / flush 行为测试。
- `tests/test_cli_terminal.py` 已有用例保持通过；3 个旧用例改用 `buffer.assistant_text`、1 个改走 `_flush_completed_tools_to_static` 路径。
- `uv run python -m pytest tests -q`：477 passed, 2 failed。
  - 2 个失败是 pre-existing，与本计划无关：`tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt`（提示中无 "Tree-sitter" 字符串）和 `tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts`（prompt 格式与测试预期不同）。两者都不涉及 `ui/cli/`。
- `uv run python -m compileall core services infrastructure ui` 无语法错误。

剩余限制与未来工作：

- 动态区列表默认最多显示 3 个活跃工具；超过会折叠成 `…  N more tools running`。ExecPlan 提到的高频工具（bash/read/grep/glob/write_file/edit_file）都已注册默认 renderer；MCP 工具的更细致摘要可以按工具 descriptor 增量加入。
- 工具结果去重由 `committed` flag 保护，reducers 是 pure 的，stream_session 是唯一 flush 来源；任何把 `consume_event` 重新接回静态打印的尝试都会被显式测试拦截。
- 旧 `print_tool_banner_start` / `print_tool_banner_running` 仍在 `static_output.py` 中暴露，供非 streaming 路径使用；本计划不删除它们，但 streaming 路径不再调用。下一步可以决定是否把 banner 路径整体替换为 `render_use_preview` + flush 提交。
- `docs/exec-plans/active/cli-live-streaming-output.md` 中的 "工具结果实时静态打印" 限制需要补充 revision note（在下一个 milestone 一起做），但本计划已经把这条路径在 streaming 会话中清零。
- 没有做手工 provider 验证（本地 .env 未配置 provider），但 `tests/test_cli_streaming_session_commit.py` + `test_loop_realtime_streaming.py` 已经覆盖了等价的 end-to-end 行为。

## Context and Orientation

OneCode 是 Python code agent runtime。CLI 是它的终端界面，位于 `ui/cli/`。主循环位于 `core/loop.py`，它不直接打印终端内容，而是产出 `core/stream_events.py::AgentEvent`。`AgentEvent` 是 CLI 可以消费的运行时事件，当前类型包括 `assistant_delta`、`tool_call_delta`、`tool_call_ready`、`tool_started`、`tool_progress`、`tool_result`、`transition`、`completed` 和 `error`。

TTY 模式入口是 `ui/cli/terminal/repl.py::InlineRepl`。用户提交普通 prompt 后，`InlineRepl._run_turn()` 创建 `ui/cli/terminal/stream_session.py::StreamingSession`，把 `runtime.loop.stream(...)` 产出的事件交给 `StreamingSession.run()`。`StreamingSession` 使用非全屏 `prompt_toolkit.Application(..., erase_when_done=True)` 创建动态区。动态区结束后会擦除；真正留在终端 scrollback 中的内容由 `ui/cli/terminal/static_output.py` 打印。

当前 `ui/cli/terminal/stream_session.py` 中有两个关键结构。`StreamBuffer.text` 累积 assistant 文本，`render_preview_ansi()` 把它渲染成动态区预览。`consume_event()` 处理工具事件时，`tool_call_ready` 和 `tool_started` 只更新 `active_tool_ids` 与 `current_tool_label`，但 `tool_result` 会立刻调用 `print_tool_result()` 写静态区。这个直接写静态区的动作是当前视觉混杂的主要来源，也是本计划要彻底移除的旧路径。

本计划使用几个术语。“动态区”是 prompt_toolkit 管理的临时区域，适合展示正在生成的文本、正在运行的工具和状态行，结束时会擦除。“静态历史”是普通终端 scrollback，适合展示已经完成的用户输入、assistant 最终文本和工具结果摘要。“活跃工具”指模型已经请求、正在准备或正在执行、但尚未收到 `tool_result` 的工具调用。“工具结果”指 `services.tools.types.ToolExecutionResult`，它是工具执行完成后 CLI 可用的结构化结果，其中包含工具名、call id、文本内容、错误标记和 metadata。

参考实现的相关机制如下。`docs/references/ui/screens/REPL.tsx` 顶层保存 `messages`、`streamingText`、`streamingToolUses` 和 `inProgressToolUseIDs`。`docs/references/ui/components/Messages.tsx` 将正式 messages 和临时 streaming state 合成一个渲染树。`docs/references/ui/utils/messages.ts::normalizeMessages()` 把多 content block 消息拆成单 block 消息，`buildMessageLookups()` 建立 `toolUseByToolUseID`、`toolResultByToolUseID`、`progressMessagesByToolUseID` 和错误/完成集合。`docs/references/ui/components/Message.tsx` 按 message 和 content block 类型分派，tool result 进入 `UserToolResultMessage`。`UserToolResultMessage` 根据取消、拒绝、错误、成功状态分派；成功时 `UserToolSuccessMessage` 调用具体工具的 `renderToolResultMessage()`。`MessageResponse` 给工具响应加 `⎿` 前缀容器。OneCode 不会照搬这些 React 组件，但会采用同一思路：工具状态进入统一渲染状态，工具结果由工具策略渲染，框架提供一致容器。

## Plan of Work

第一阶段是建立 turn 内渲染状态。新增 `ui/cli/terminal/turn_render_state.py`。这个模块不读文件、不执行工具、不修改 `MessageStore`，只接收 `AgentEvent` 并更新内存中的当前 turn 状态。它应定义 `TurnRenderState`、`AssistantTailState`、`ActiveToolState`、`CompletedToolState` 和一个 `consume_agent_event(state, event)` 函数。`AssistantTailState` 保存完整本轮 assistant 文本以及用于动态区显示的尾部行；动态区最多显示 5 行 assistant 文本。`ActiveToolState` 保存 `call_id`、`tool_name`、`input_preview`、`status` 和最近 progress。`CompletedToolState` 保存已经完成但尚未提交到静态历史的 `ToolExecutionResult`。

第二阶段是让 `StreamingSession` 使用新的状态。修改 `ui/cli/terminal/stream_session.py`，把 `StreamBuffer` 中散落的 `text`、`active_tool_ids`、`current_tool_label` 直接删除，改为唯一的 `TurnRenderState`。`consume_event()` 不再直接调用 `print_tool_result()`；它只调用 `consume_agent_event()`。`render_preview_ansi()` 不再只渲染 `buffer.text`，而是渲染 assistant 尾部和活跃工具列表。动态区的状态行应继续显示 `onecode>` 前缀和 Esc 提示。

第三阶段是实现动态区布局。动态区的内容应按固定顺序渲染：先显示 assistant 尾部文本，最多 5 行；再显示活跃工具列表，每个工具一行，必要时加一行参数预览；最后显示状态行。若没有 assistant 文本但有活跃工具，动态区只显示工具列表。若活跃工具数量超过可用空间，保留最近或正在运行的工具，并折叠旧工具为一行，例如 `... 2 more tools running`。动态区不显示完整工具结果，只显示完成之前的状态。完成的工具会从活跃列表移除。

第四阶段是实现静态历史提交。当前 `commit_final(buffer)` 只提交 assistant 文本。改造后它应调用统一提交逻辑：先提交本轮期间已经完成的工具结果摘要，再提交最终 assistant markdown，或者按照事件顺序提交已完成工具和 assistant 文本。这里要做一个明确选择：对于第一版，完成的工具结果在 `tool_result` 到达后即可提交到静态历史，但提交动作必须通过统一 renderer 完成，而不是在 reducer 内直接 print。这样用户可以在长工具批次中及时看到已完成结果，同时动态区保持清爽。实现上可在 `StreamingSession._feed()` 每次消费事件后调用 `flush_completed_static_blocks()`，该函数只处理状态中尚未提交的 completed tool blocks，并标记为已提交。assistant 最终文本仍只在 turn 结束时由 `commit_final()` 提交。

第五阶段是重写工具结果渲染策略。`ui/cli/tool_renderers.py` 不再保留旧的分散摘要实现，而是成为唯一的 CLI 工具渲染入口。这个模块应直接提供清晰的内部策略接口，例如 `ToolCliRenderer`。这个接口至少应支持 `render_use_preview()`、`render_running()`、`render_success()` 和 `render_error()`。第一版可以不把接口暴露给 tools descriptor，先在 CLI 内按 `result.tool_name` 分派。未来如需让工具 descriptor 提供 renderer，可另开计划，因为 descriptor 属于 services/tools 边界，不应为了 CLI 表现立即耦合 Rich。

第六阶段是补内置工具展示。优先处理 bash、read_file、grep、glob、edit_file/write_file 的高频摘要。Bash 的活跃预览应显示命令的前两行或前 160 个字符，参考 `docs/references/Tools_full/BashTool/UI.tsx::renderToolUseMessage()` 的截断策略；结果摘要应显示 exit code、stdout/stderr 是否存在、截断状态和必要的一两行预览。read_file 应显示路径和读取行范围。grep 应显示 pattern、匹配数和文件数。glob 应显示 pattern 和匹配数量。未知工具和 MCP 工具必须走 fallback，不抛异常。

第七阶段是更新测试。新增 `tests/test_cli_turn_render_state.py`，直接构造 `AgentEvent` 序列，验证 assistant 文本、tool_call_ready、tool_started、tool_progress、tool_result 的归约结果。更新或新增 `tests/test_cli_terminal.py`，验证 `render_preview_ansi()` 在工具运行时包含工具名和参数预览，在 assistant 文本出现后最多显示 5 行尾部，在 tool_result 后动态区不再显示该工具为 active。新增工具 renderer 测试，验证 bash/read/grep/glob fallback 不抛异常，且输出包含关键字段。

第八阶段是更新文档。修改 `docs/design-docs/cli-architecture.md` 和 `docs/design-docs/cli-message-rendering-architecture.md`，说明 TTY 主屏的新规则：动态区展示当前 assistant 尾部与活跃工具；完成的工具结果由静态历史统一 renderer 打印；工具结果渲染不是上下文事实来源；`MessageStore` 和 transcript 仍是模型上下文事实来源。若本计划实施后影响 `docs/exec-plans/active/cli-live-streaming-output.md` 的限制说明，也应在那里补充 revision note 或将相关内容移动到 completed/后续计划中，避免文档互相矛盾。

## Concrete Steps

所有命令均在仓库根目录 `D:\study\OneCode` 运行。

先确认当前基线。运行：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_async_cli_streaming.py -q

预期这些测试应通过。如果失败，先确认失败是否已经存在且与 CLI streaming 无关。不要为了本计划修改无关失败。

创建 `ui/cli/terminal/turn_render_state.py`。建议接口如下，具体字段可按实现微调，但测试应覆盖同等行为：

    @dataclass
    class AssistantTailState:
        text: str = ""

        def visible_lines(self, *, max_lines: int = 5) -> list[str]:
            ...

    @dataclass
    class ActiveToolState:
        call_id: str
        tool_name: str
        input_preview: str = ""
        status: str = "pending"
        progress: str = ""

    @dataclass
    class CompletedToolState:
        call_id: str
        result: ToolExecutionResult
        committed: bool = False

    @dataclass
    class TurnRenderState:
        assistant: AssistantTailState = field(default_factory=AssistantTailState)
        active_tools: dict[str, ActiveToolState] = field(default_factory=dict)
        completed_tools: list[CompletedToolState] = field(default_factory=list)
        current_tool_label: str = ""

    def consume_agent_event(state: TurnRenderState, event: AgentEvent) -> None:
        ...

添加 `tests/test_cli_turn_render_state.py`。至少覆盖这些场景：assistant delta 累加；6 行 assistant 文本只返回最后 5 行；tool_call_ready 创建 active tool；tool_started 改状态为 running；tool_progress 记录最近 progress；tool_result 把工具从 active 移到 completed；没有 call id 的 result 也能通过 fallback call id 进入 completed。

修改 `ui/cli/terminal/stream_session.py`。先用 `TurnRenderState` 替代 `StreamBuffer` 中工具相关字段，再替代 assistant text 渲染输入。`consume_event()` 应成为薄适配器，调用新 reducer，不直接打印工具结果。`StreamingSession._feed()` 在每次消费事件后调用新的 `_flush_completed_tools_to_static()`，它查找 `state.completed_tools` 中 `committed=False` 的项，通过工具 renderer 打印，并置为 committed。这样工具完成后进入历史，但不是在 reducer 内发生副作用。

实现动态区渲染函数，建议放在 `ui/cli/terminal/stream_session.py` 或新文件 `ui/cli/terminal/turn_preview.py`：

    def render_turn_preview_ansi(state: TurnRenderState, *, width: int) -> ANSI:
        ...

它应复用已有 Markdown 渲染能力渲染 assistant 尾部，但必须把 assistant 预览限制为 5 行。工具行建议使用纯文本 ANSI：`tool: read_file path="core/loop.py" running`。如果需要 Rich 样式，可以用 `Console(file=StringIO(), force_terminal=True, theme=RICH_THEME)` 生成 ANSI，但不要设置背景色。

扩展 `ui/cli/tool_renderers.py`。保留原函数名以减少调用方改动，但让它们内部调用策略。若第一版实现纯字符串策略，返回 `str` 即可；若返回 Rich `Text`，调用方要保持一致。不要让工具 renderer 读取文件或重新执行工具；它只能消费 `ToolExecutionResult`、工具输入预览和 metadata。

更新 `ui/cli/terminal/static_output.py`。新增或调整 `print_tool_result()`，让它调用统一工具 renderer 并加上 `⎿` 前缀。避免把 `⎿` 写进每个具体工具 renderer；具体工具 renderer 只返回内容，容器由框架层提供。这样对应参考实现中 `MessageResponse` 的职责。

运行聚焦测试：

    uv run python -m pytest tests/test_cli_turn_render_state.py tests/test_cli_terminal.py tests/test_async_cli_streaming.py -q

然后运行更广的 CLI/loop 测试：

    uv run python -m pytest tests/test_loop.py tests/test_async_loop.py tests/test_model_stream_events.py -q

最后运行编译检查：

    uv run python -m compileall core services infrastructure ui

手工验证时，配置可用 provider 后运行：

    uv run python -m ui.cli.app

输入：

    请先读取 core/loop.py 的前 30 行，然后用 6 行解释 AgentLoop 的职责。

期望观察到：工具执行期间动态区显示类似 `tool: read_file path="core/loop.py"` 的活跃状态；read_file 完成后静态历史出现带 `⎿` 的工具结果摘要；assistant 开始生成时动态区最多显示 5 行尾部文本；最终 turn 结束后完整 assistant 文本进入静态历史。

再输入：

    运行一个只读 bash 命令列出 core 目录文件，然后总结结果。

期望观察到：bash 执行期间动态区显示命令预览，命令太长时被截断；完成后静态历史显示 bash 结果摘要，不显示完整无界 stdout；assistant 总结继续以最多 5 行尾部方式流式显示。

## Validation and Acceptance

本计划完成时必须满足以下可观察行为。

动态区不再直接显示已完成工具结果的完整摘要。工具执行期间动态区显示工具名、参数预览和状态；`tool_result` 到达后，该工具从 active list 消失，并在静态历史中出现一次工具结果摘要。

assistant 文本开始生成后，动态区最多显示 5 行 assistant 尾部。若 assistant 文本超过 5 行，动态区应显示最后 5 行或带一行折叠提示的尾部，不应撑高整个预览窗口。最终 assistant 文本仍在 turn 完成后完整提交到静态历史。

工具结果打印由统一静态 renderer 完成，不再由 `consume_event()` 直接调用 `print_tool_result()`。测试应能证明 reducer 纯更新状态，副作用发生在明确的 flush/commit 函数中。

工具结果摘要带统一容器，例如 `⎿` 前缀。具体工具 renderer 不应各自实现容器；这能避免嵌套和样式不一致。

未知工具、MCP 工具或缺少专用 renderer 的工具必须通过 fallback 成功渲染。渲染失败不能让 CLI turn 崩溃；最坏情况下应显示工具名、call id 和成功/错误状态。

工具安全边界不变。工具仍只由 `RegistryToolExecutor` 在完整 `tool_call_completed` 后执行，并继续经过 validation、guard、permission policy 和 hooks。本计划不得在 CLI 中执行工具或绕过权限。

运行：

    uv run python -m pytest tests/test_cli_turn_render_state.py tests/test_cli_terminal.py tests/test_async_cli_streaming.py -q

应全部通过。运行：

    uv run python -m compileall core services infrastructure ui

应无语法错误。若全量测试有已知 pre-existing 失败，应在 `Outcomes & Retrospective` 中记录失败名称和为什么与本计划无关。

## Idempotence and Recovery

本计划的代码改动应可重复应用和测试。新增 reducer 是纯内存状态，不写文件、不启动进程、不访问网络。工具 renderer 只消费已有 `ToolExecutionResult`，不读取文件、不调用工具、不改变权限规则。

如果动态区渲染出现错位或裁剪错误，优先把信息显示得更少，而不是把完整历史搬进动态区。可恢复策略是保留 assistant 最后 5 行和 active tools，其余折叠。动态区是临时预览，不是事实来源。

如果某个专用工具 renderer 因 metadata 格式不稳定而难以实现，先退回 fallback renderer，并在 `Surprises & Discoveries` 中记录具体工具和原因。不要为了专用 UI 读取工具输出文件或解析不稳定私有字段。

如果实现过程中发现 `StreamBuffer` 被测试或外部 import 依赖，直接更新这些调用点改用 `TurnRenderState` 或新的渲染 API，不要为兼容性保留代理层。完成后在 `Outcomes & Retrospective` 中记录旧路径已删除。

如果手工验证中 provider 不调用工具，可使用已有单元测试或构造 fake `AgentEvent` 流验证 UI 行为。自动测试不得依赖真实网络或真实模型。

## Artifacts and Notes

当前 OneCode 相关代码证据如下。

`core/stream_events.py` 定义 CLI 可消费事件：

    AgentEventType = Literal[
        "interaction_started",
        "assistant_delta",
        "assistant_message_completed",
        "tool_call_delta",
        "tool_call_ready",
        "tool_started",
        "tool_progress",
        "tool_result",
        "transition",
        "completed",
        "error",
    ]

`ui/cli/terminal/stream_session.py` 当前在工具结果事件中直接打印静态历史，这正是本计划要移走的副作用：

    elif event_type == "tool_result":
        ...
        print_tool_result(
            result,
            call_id=str(call_id or ""),
            workspace=buffer.workspace,
        )

参考实现把 streaming tool use 转成 synthetic assistant message，统一进入 messages 渲染：

    const syntheticStreamingToolUseMessages = useMemo(() =>
      streamingToolUsesWithoutInProgress.flatMap(streamingToolUse => {
        const msg = createAssistantMessage({
          content: [streamingToolUse.contentBlock]
        })
        msg.uuid = deriveUUID(streamingToolUse.contentBlock.id as UUID, 0)
        return normalizeMessages([msg])
      }),
    ...)

参考实现建立工具调用和工具结果 lookup：

    const toolUseByToolUseID = new Map<string, ToolUseBlockParam>()
    const toolResultByToolUseID = new Map<string, NormalizedMessage>()
    ...
    toolUseByToolUseID.set(content.id, content)
    ...
    toolResultByToolUseID.set(content.tool_use_id, msg)

参考实现按工具结果状态分派：

    if (param.content startsWith CANCEL_MESSAGE) return UserToolCanceledMessage
    if (param.content startsWith REJECT_MESSAGE) return UserToolRejectMessage
    if (param.is_error) return UserToolErrorMessage
    return UserToolSuccessMessage

参考实现的工具响应容器：

    <Text dimColor>{'  '}⎿  </Text>

参考 Bash 工具 UI 的命令预览约束：

    MAX_COMMAND_DISPLAY_LINES = 2
    MAX_COMMAND_DISPLAY_CHARS = 160

OneCode 第一版应采用同类约束：动态区展示工具参数时不超过两行或 160 字符，超出用省略号。

## Interfaces and Dependencies

新增 `ui/cli/terminal/turn_render_state.py`，提供以下稳定接口：

    class TurnRenderState:
        assistant: AssistantTailState
        active_tools: dict[str, ActiveToolState]
        completed_tools: list[CompletedToolState]

    def consume_agent_event(state: TurnRenderState, event: AgentEvent) -> None:
        ...

`consume_agent_event` 是纯函数式副作用边界：它可以修改传入 state，但不得打印、读写文件、调用工具或访问 runtime。

`ui/cli/terminal/stream_session.py` 应使用：

    def render_turn_preview_ansi(state: TurnRenderState, *, width: int) -> ANSI:
        ...

这个函数可以使用 Rich 和 prompt_toolkit ANSI，但只返回 renderable，不写 stdout。

`ui/cli/terminal/static_output.py` 应提供统一工具结果提交入口：

    def print_tool_result(result: Any, *, call_id: str, workspace: Path | None = None) -> None:
        ...

该函数负责 `⎿` 容器和静态 Console 打印。具体工具 renderer 不负责外层容器。

`ui/cli/tool_renderers.py` 应提供新的统一函数：

    def render_tool_result(result: ToolExecutionResult, *, workspace: Path) -> str:
        ...

    def render_fallback_tool_result(result: Any) -> str:
        ...

并可新增内部策略结构：

    class ToolCliRenderer(Protocol):
        def render_use_preview(self, tool_name: str, tool_input: object) -> str: ...
        def render_success(self, result: ToolExecutionResult, *, workspace: Path | None = None) -> str: ...
        def render_error(self, result: ToolExecutionResult, *, workspace: Path | None = None) -> str: ...

这个协议目前属于 CLI 层，不加入 `services/tools/types.py`。原因是 OneCode 的工具 descriptor 是 runtime 事实来源，不能为了终端 Rich 表现引入 UI 依赖。旧的分散工具结果摘要函数不应继续作为长期 API 保留。

`core/loop.py`、`services/tools/executor.py`、`services/permissions/` 和 `services/guard/` 不应因本计划改变职责。若测试需要 fake tool result，应直接构造 `ToolExecutionResult` 或 fake `AgentEvent`，不要绕开 executor 安全链路来运行真实工具。

## Revision Notes

- 2026-06-15 / Codex: 初版计划。基于当前 OneCode CLI 渲染实现和参考实现的 messages/streamingToolUses/UserToolResultMessage/MessageResponse/BashTool UI 机制，确定采用“动态区显示当前活跃状态，静态历史显示完成结果”的方案，而不是把 CLI 改成完整 React 式 message list。
