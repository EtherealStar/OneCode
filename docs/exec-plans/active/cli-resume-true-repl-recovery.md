# 重构 CLI Resume 为真正恢复到可继续交互的 REPL

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文档遵循仓库根目录 `PLANS.md`。后续实现者必须按 `PLANS.md` 维护本文：每次推进实现、发现事实、改变决策或完成验收，都要同步更新本文，并保持本文自包含。写入本 `.md` 文件时不需要外层 fenced code block。

## Purpose / Big Picture

完成此变更后，用户在 OneCode CLI 输入 `/resume`，在会话选择页按 Enter 选中一个历史会话，就会立即回到主终端 REPL，并在该会话的上下文中继续输入和运行 agent。用户不需要二次确认，也不需要从历史 page 按 Esc 返回；选择器只是选择器，不是历史查看器。

恢复后的主终端 scrollback 必须像正常会话当时产生的一样显示历史消息。用户消息必须使用当前正常输入同样的反色渲染；assistant 消息必须使用当前正常 assistant 定稿 Markdown 渲染；工具结果必须复用当前正常工具结果渲染路径。不能为 resume 单独发明摘要格式，也不能保留旧的“恢复历史 page”兼容路径。

用户可通过启动 `uv run python -m ui.cli.app`，输入 `/resume`，选择一个已有会话，然后直接继续输入新问题来观察效果：历史对话出现在主终端 scrollback 中，底部输入框重新出现，新输入会追加到恢复后的 session transcript。

## Progress

- [x] (2026-06-19 00:00+08:00) 已阅读 `PLANS.md`，确认 ExecPlan 必须自包含、可执行、持续维护，并且写入 `.md` 文件时省略外层 fenced code block。
- [x] (2026-06-19 00:00+08:00) 已阅读 `architecture.md`、`docs/design-docs/core-beliefs.md`、`docs/design-docs/cli-architecture.md`、`docs/design-docs/context-architecture.md`、`docs/design-docs/core-runtime-architecture.md`、`docs/design-docs/cli-message-rendering-architecture.md`，确认本功能属于 `ui/cli` 交互层和 context/session 恢复装配，不应改写 `core/loop.py` 的主循环职责。
- [x] (2026-06-19 00:00+08:00) 已阅读当前 OneCode resume 相关代码：`ui/cli/resume.py`、`ui/cli/commands.py`、`ui/cli/terminal/repl.py`、`ui/cli/terminal/selector.py`、`ui/cli/terminal/static_output.py`、`ui/cli/renderer.py`、`ui/cli/types.py`。
- [x] (2026-06-19 00:00+08:00) 已阅读用户提供的参考文件入口和关键恢复工具：`docs/references/ui/screens/ResumeConversation.tsx`、`docs/references/ui/screens/REPL.tsx`、`docs/references/ui/utils/sessionStorage.ts`、`docs/references/ui/utils/conversationRecovery.ts`、`docs/references/ui/utils/sessionRestore.ts`。
- [x] (2026-06-19 00:00+08:00) 已确认当前 OneCode 的主要差距：恢复成功后仍走 `presentation="page"` 和 `renderer.render_restored_messages()`，导致用户看到的是临时历史页，而不是回到可继续交互的主 REPL。
- [x] Milestone 1：收敛 resume 结果模型，删除恢复成功后的 page 语义，让直接 `/resume <target>` 和交互式选择器都返回同一个“恢复 runtime + 待重放消息”的结果。
- [x] Milestone 2：新增正常会话静态重放器，按当前正常 scrollback 渲染路径重放 user、assistant 和 tool_result，不保留恢复专用摘要格式。
- [x] Milestone 3：把 `InlineRepl` 的 resume selector 接入新结果模型，Enter 选中会话后立即恢复 runtime、重放历史并回到底部输入框。
- [x] Milestone 4：删除旧兼容路径和文档描述，包括 `renderer.render_restored_messages()`、`renderer._restored_message_line()` 以及恢复历史 page 的测试断言。
- [x] Milestone 5：补充聚焦测试、编译检查和手动验收，证明恢复后可以继续交互，且历史消息渲染与正常会话一致。
- [x] (2026-06-19 / Codex) 已实现并通过聚焦测试：`uv run python -m pytest tests/test_cli_resume.py tests/test_cli_terminal.py tests/test_cli_commands.py tests/test_import_boundaries.py -q` → 105 passed。`uv run python -m compileall ui core services` 通过。全量 `pytest tests -q` 为 2 failed / 557 passed，两处失败 `tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt` 与 `tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts` 属于工具 prompt 描述格式断言，与本计划无关（本计划未改 `tools/`、`prompts/sections.py`）。

## Surprises & Discoveries

- Observation: 当前 `restore_runtime_from_target()` 已经完成大部分 runtime 恢复装配，并通过 `CliRuntime.with_session()` 重绑定 session-scoped 资源。
  Evidence: `ui/cli/resume.py::restore_runtime_from_target()` 创建新的 `RuntimeState` 和 `MessageStore.from_transcript(...)`，再调用 `runtime.with_session(...)`；`ui/cli/types.py::CliRuntime.with_session()` 会切换 trace/error recorder、清理 current model context、重绑 subagent parent store、result store、compaction service、file state cache 和 attachment collector。

- Observation: 当前 UI 恢复路径把历史渲染成 page，而不是主终端 scrollback。
  Evidence: `ui/cli/commands.py::_resume()` 成功后返回 `presentation="page"`，并把 `renderer.render_resume(...)` 与 `renderer.render_restored_messages(...)` 组合成 renderable；`ui/cli/terminal/repl.py::_run_resume_selector()` 选中会话后也返回 `presentation="page"`。

- Observation: 当前 `renderer.render_restored_messages()` 是恢复专用摘要视图，不等价于正常会话渲染。
  Evidence: `ui/cli/renderer.py::_restored_message_line()` 对 assistant 工具调用打印 `assistant: <tool call: ...>`，对 tool result 打印 `[tool_name call_id ok/error]`；正常会话静态输出则走 `ui/cli/terminal/static_output.py::print_user_submitted`、`print_assistant_markdown` 和 `print_tool_result`。

- Observation: 用户消息反色已有正常路径，恢复时应复用它，而不是在 renderer 中另写一份。
  Evidence: `ui/cli/terminal/static_output.py::print_user_submitted()` 调用 `user_reverse_style(brightness)`；`tests/test_cli_terminal.py::test_user_submitted_uses_reverse_style_dark` 已覆盖正常用户输入反色。

- Observation: 参考实现的关键机制不是“打开历史页”，而是选择后把加载出的消息作为 REPL 初始消息继续运行。
  Evidence: `docs/references/ui/screens/ResumeConversation.tsx` 中搜索 `function onSelect`、`loadConversationForResume`、`switchSession`、`restoreAgentFromSession`、`restoreWorktreeForResume` 和 `return <REPL`，可以看到选择后加载完整会话、恢复状态，然后渲染 `<REPL initialMessages={resumeData.messages} ... />`。

## Decision Log

- Decision: Resume 成功后不再使用 `presentation="page"` 展示历史。
  Rationale: 用户目标是“选中对话后恢复到终端界面并继续交互”，page 是临时备用屏幕，退出后不进入主 scrollback，和参考实现中直接进入 REPL 的机制不一致。
  Date/Author: 2026-06-19 / Codex。

- Decision: 不保留旧的恢复历史兼容渲染路径。
  Rationale: 用户明确要求不需要保留兼容性代码；旧路径 `renderer.render_restored_messages()` 会制造恢复专用显示规则，和“恢复历史会话在显示上的所有消息渲染应该与正常会话完全相同”冲突。
  Date/Author: 2026-06-19 / Codex。

- Decision: 恢复历史重放必须复用正常静态区渲染函数。
  Rationale: 只有走 `print_user_submitted`、`print_assistant_markdown` 和 `print_tool_result`，才能保证恢复历史和正常会话 scrollback 一致。未来正常工具结果渲染策略升级时，resume 会自动跟随。
  Date/Author: 2026-06-19 / Codex。

- Decision: 不在 `core/loop.py` 中添加 resume 分支。
  Rationale: `core/loop.py` 是薄主循环，只负责上下文重建、模型调用、工具执行和 transition；resume 是 CLI 命令、session 持久化和 UI 重放问题，应留在 `ui/cli` 与 `services/context` 边界。
  Date/Author: 2026-06-19 / Codex。

- Decision: 交互式 `/resume` 在 Enter 选中后立即恢复，不做二次确认。
  Rationale: 用户明确要求选中后直接恢复；参考实现 `ResumeConversation.tsx::onSelect` 也是选择后立即加载并切换到 REPL。
  Date/Author: 2026-06-19 / Codex。

## Outcomes & Retrospective

已实施（2026-06-19 / Codex）。最终行为与删除项如下。

最终行为：`/resume <target>` 和无参 `/resume` 选择器在选中后都恢复 runtime 并返回 `presentation="inline"` + 一行 `renderer.render_resume()` 通知 + `replay_messages`（恢复后的当前消息链）。`InlineRepl._handle_command()` 在切换 runtime、重置 prompt session、打印 inline 通知后，调用 `ui/cli/terminal/transcript_replay.py::replay_messages_to_static()` 把历史消息按正常静态输出函数重放进主 scrollback，然后回到底部输入框继续交互。

复用的正常渲染路径：user → `print_user_submitted()`（反色），有正文 assistant → `print_assistant_markdown()`，tool_result → 重建 `ToolExecutionResult` 后走 `print_tool_result()`。只有 tool call 没有正文的 assistant 不打印；attachment 不重放。

删除的旧路径：`ui/cli/renderer.py::render_restored_messages()` 与 `_restored_message_line()`（及其私有 `_tool_call_names()` 辅助）已删除。`commands.py::_resume()` 和 `repl.py::_run_resume_selector()` 不再调用它们，也不再返回恢复历史 page。`renderer.render_resume()` 保留（恢复成功通知），`renderer.render_history()` 保留（诊断视图）。

测试结果：`uv run python -m compileall ui core services` 通过；聚焦套件 `tests/test_cli_resume.py tests/test_cli_terminal.py tests/test_cli_commands.py tests/test_import_boundaries.py` 105 passed。全量 `pytest tests -q` 为 557 passed / 2 failed，两处失败与工具 prompt 描述格式相关（`tools/`、`prompts/sections.py`），不在本计划范围内，已记录原因，未通过改无关代码掩盖。

测试改动：`tests/test_cli_resume.py::test_resume_command_replaces_runtime_and_restores_messages` 改为断言 `presentation=="inline"`、`replay_messages` 等于恢复消息链、renderable 仅含恢复通知。`tests/test_cli_terminal.py` 把旧的 `_restored_message_line` 断言改写为针对 `replay_messages_to_static()` 的两条测试（反色 user 行 + 三类消息复用正常静态渲染）。

后续可选工作：跨项目 resume、标题搜索增强、启动时 `--resume`、attachment 的统一静态渲染（正常会话与 resume 同时接入后再重放）、transcript 多 leaf / orphan tool result / 中断 turn 的健壮性扩展（参考 `docs/references/ui/utils/conversationRecovery.ts`）。

## Context and Orientation

OneCode 是一个 Python code agent runtime。`core/loop.py::AgentLoop` 是 agent 主循环，负责把用户 prompt 写入 `MessageStore`、构建模型上下文、调用模型、执行工具并产出 `AgentEvent` 流。CLI 位于 `ui/cli/`，是当前用户交互界面。TTY 路径由 `ui/cli/app.py::main()` 构建 `CliRuntime` 后启动 `ui/cli/terminal/repl.py::InlineRepl`。

本文使用几个术语。REPL 指 Read-Eval-Print Loop，也就是终端里反复读取用户输入、运行 agent、打印结果、再等待下一次输入的主交互界面。静态区指普通终端 scrollback，一旦打印就可向上滚动查看；当前由 `ui/cli/terminal/static_output.py` 和流式路径中的 `ui/cli/terminal/output_coordinator.py::TerminalOutputCoordinator` 写入。动态区指 prompt_toolkit 的可擦除输入和流式预览区域，结束后不会进入 scrollback。备用屏幕指 `ui/cli/terminal/page.py` 和 `selector.py` 使用的全屏临时界面，适合选择器或状态页，但不适合保存恢复后的历史。

当前 resume 相关文件如下。`ui/cli/resume.py` 负责扫描 `.onecode/<session_id>/messages.jsonl`、解析目标、从 transcript 构建新的 `MessageStore` 并恢复部分 session state。`ui/cli/commands.py::_resume()` 注册并处理 `/resume` 和 `/continue`。`ui/cli/terminal/repl.py::_run_resume_selector()` 处理无参数 `/resume` 的交互式选择器。`ui/cli/types.py::CliRuntime.with_session()` 是切换 session 后重建 runtime 资源的关键装配点。`ui/cli/terminal/static_output.py` 定义正常主屏的静态输出函数。`ui/cli/renderer.py::render_restored_messages()` 是当前旧的恢复专用历史视图，应在本计划中删除。

当前正常会话的主屏渲染路径已经在 `docs/design-docs/cli-message-rendering-architecture.md` 中描述。用户输入由 `print_user_submitted()` 打印并反色；assistant 定稿由 `print_assistant_markdown()` 打印；工具结果由 `print_tool_result()` 打印。恢复历史必须复用这些函数，而不是通过 table、page 或恢复专用摘要渲染。

用户提供的参考实现是 TypeScript/React/Ink 架构，不能逐字照搬，但它清楚表达了目标机制。`docs/references/ui/screens/ResumeConversation.tsx` 展示选择器如何加载日志、在 `onSelect` 中恢复状态，并把恢复后的消息传给 `<REPL initialMessages=...>`。`docs/references/ui/utils/conversationRecovery.ts` 展示如何把 transcript 加载、清洗和反序列化成可恢复会话。`docs/references/ui/utils/sessionRestore.ts` 展示如何恢复 agent、worktree、metadata、cost 和其他会话状态。`docs/references/ui/utils/sessionStorage.ts` 展示如何加载同仓库和所有项目的会话列表、渐进式 enrich lite logs、按 parent chain 构建会话链。`docs/references/ui/screens/REPL.tsx` 展示 REPL 接收 `initialMessages` 后继续作为主交互界面运行。

## Plan of Work

Milestone 1 先收敛 resume 的结果模型。编辑 `ui/cli/types.py::CommandResult`，增加一个字段表达“命令成功后需要按正常主屏规则重放的消息”，例如 `replay_messages: tuple[dict[str, Any], ...] = ()`。这个字段不是模型上下文事实来源，只是 UI 在主终端静态区重放历史的请求。编辑 `ui/cli/commands.py::_resume()`，让直接 `/resume <target>` 成功后返回 `runtime=resumed`、`renderable=renderer.render_resume(...)`、`presentation="inline"` 和 `replay_messages=resumed.message_store.current_messages()`。不要再组合 `renderer.render_restored_messages()`，也不要再返回 `presentation="page"`。错误路径和多标题匹配路径可以继续用当前 page 或 inline 行为，因为它们不是恢复成功路径。

Milestone 2 新增正常会话静态重放器。建议创建 `ui/cli/terminal/transcript_replay.py`，定义一个清晰的入口，例如 `replay_messages_to_static(messages, *, brightness, workspace) -> None`。它按 message 顺序读取 `role` 并调用正常静态输出函数。`role == "user"` 时提取文本并调用 `print_user_submitted(text, brightness=brightness)`，确保历史用户输入和当前用户输入一样反色。`role == "assistant"` 时如果有可显示文本，调用 `print_assistant_markdown(text)`；如果 assistant 只有 tool calls 且没有文本，不要发明 `assistant: <tool call>` 这种恢复专用行，因为正常 scrollback 当前也不这样打印。`role == "tool_result"` 时把 message 转成 `services.tools.types.ToolExecutionResult`，再调用 `print_tool_result(result, call_id=result.tool_call_id, workspace=workspace)`。`role == "attachment"` 当前正常主屏没有稳定静态渲染，因此恢复时也不要额外打印，除非同一 milestone 先把正常会话和恢复会话同时接入同一个 attachment 静态渲染函数。这个 milestone 的验收是同一组 messages 用新 replay 函数打印后，user、assistant 和 tool result 的文本形态分别与正常静态输出函数一致。

Milestone 3 把 `InlineRepl` 接入新结果模型。编辑 `ui/cli/terminal/repl.py::_handle_command()`，在处理完 `result.runtime` 和 `_reset_prompt_session()` 后，如果 `result.renderable` 是 inline 就打印恢复通知，然后如果 `result.replay_messages` 非空就调用 `replay_messages_to_static(...)`。对于 `result.presentation == "page"` 的非恢复命令，仍然按当前 `_show_page()` 处理。编辑 `ui/cli/terminal/repl.py::_run_resume_selector()`，让选择器按 Enter 选中后调用 `restore_runtime_from_target(...)`，并返回与直接 `/resume <target>` 相同形态的 `CommandResult`：inline 恢复通知、runtime、replay messages。不要再返回恢复历史 page。这样 selector 自身退出备用屏幕后，主屏会立刻打印恢复通知和历史，然后回到底部输入框。

Milestone 4 删除旧兼容路径。删除 `ui/cli/renderer.py::render_restored_messages()` 和 `_restored_message_line()`，并删除或改写只服务旧恢复 page 的测试。更新 `docs/design-docs/cli-message-rendering-architecture.md` 和 `docs/design-docs/cli-architecture.md`，把“恢复历史 page”描述改为“恢复成功后在主 scrollback 中按正常静态输出函数重放”。搜索 `render_restored_messages` 和 `_restored_message_line`，生产代码中不应再有调用。保留 `renderer.render_resume()`，它只是恢复成功通知，不是历史渲染路径。

Milestone 5 扩展恢复数据加载的健壮性，但不把参考实现的全部功能一次性搬进 OneCode。当前 OneCode transcript 是 `.onecode/<session_id>/messages.jsonl`，与参考实现的全局 projects 目录和 parentUuid chain 不同。第一版继续使用 `MessageStore.from_transcript(...)` 作为完整消息链恢复入口，并保留 `restore_session_state(...)` 对文件状态的恢复。若后续发现 OneCode transcript 中存在多 leaf、orphan tool result 或中断 turn 问题，再单独扩展 `services/context/transcript.py` 和 `ui/cli/resume.py`。本 milestone 只要求把命名和职责向参考实现靠拢，例如在 `ui/cli/resume.py` 中把 `restore_runtime_from_target()` 内部拆成“加载 conversation”和“恢复 runtime state”两个步骤，但不要新增复杂跨项目或远程 teleport 功能。

## Concrete Steps

从仓库根目录开始：

    cd D:\study\OneCode
    git status --short

工作树可能已有用户或其他 agent 的改动。不要回滚它们。只编辑本计划涉及的文件，并在每个 milestone 后运行聚焦测试。

先阅读当前实现：

    Get-Content ui\cli\types.py
    Get-Content ui\cli\commands.py
    Get-Content ui\cli\resume.py
    Get-Content ui\cli\terminal\repl.py
    Get-Content ui\cli\terminal\static_output.py
    Get-Content ui\cli\renderer.py
    Get-Content tests\test_cli_resume.py
    Get-Content tests\test_cli_terminal.py

实现 Milestone 1 后运行：

    uv run python -m pytest tests/test_cli_resume.py -q

预期旧的 `test_resume_command_replaces_runtime_and_restores_messages` 需要更新。更新后的断言应表达：恢复成功后 `result.presentation == "inline"`，`result.runtime.state.session_id` 是目标 session，`result.replay_messages` 包含完整恢复消息，`result.renderable` 只包含恢复通知，不包含历史正文。

实现 Milestone 2 和 Milestone 3 后运行：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_resume.py -q

新增测试应捕获静态输出并断言三类消息重放。用户消息输出包含用户文本且有 ANSI 样式；assistant 输出包含 `onecode>` 和 Markdown 渲染后的正文；tool result 输出包含正常 `print_tool_result()` 的容器和同一个工具 renderer 结果。交互式 selector 测试应证明按 Enter 选中后不会调用 `_show_page()`。

删除旧路径后搜索：

    rg -n "render_restored_messages|_restored_message_line|restored messages|Session History" ui tests docs

生产代码中不应再出现 `render_restored_messages` 或 `_restored_message_line`。文档中如果出现旧名称，必须是在说明“已删除旧路径”的上下文里。

最终运行：

    uv run python -m compileall ui core services
    uv run python -m pytest tests/test_cli_resume.py tests/test_cli_terminal.py tests/test_cli_commands.py -q
    uv run python -m pytest tests/test_import_boundaries.py -q

如果全量测试可用，再运行：

    uv run python -m pytest tests -q

手动验收：

    uv run python -m ui.cli.app

在 CLI 中输入 `/resume`，用方向键选择一个有 user、assistant 和工具调用记录的会话，按 Enter。期望观察到 selector 退出，主终端显示 `Restored session ...`，随后历史消息按正常会话 scrollback 风格出现，底部输入框重新出现。继续输入一句新 prompt，例如：

    继续总结上一轮的结论

期望 agent 使用恢复后的消息链继续，而不是开启空白新会话。

## Validation and Acceptance

自动化验收必须覆盖五类行为。第一，直接 `/resume <target>` 成功后不再返回 page，不再把历史正文塞进 renderable，而是返回新 runtime 和 `replay_messages`。第二，交互式 `/resume` 的 selector 按 Enter 后立即恢复，不显示二次确认，不调用历史 page。第三，恢复历史重放复用正常静态输出函数；测试应通过捕获输出证明 user、assistant 和 tool result 的形态与 `print_user_submitted`、`print_assistant_markdown`、`print_tool_result` 一致。第四，所有 user 历史消息必须反色；测试可以通过 ANSI SGR 存在和 `user_reverse_style()` 的 style 断言证明。第五，恢复后继续输入 prompt 时，`InlineRepl` 使用的是恢复后的 `CliRuntime.loop` 和 `MessageStore`。

聚焦测试命令：

    uv run python -m compileall ui core services
    uv run python -m pytest tests/test_cli_resume.py tests/test_cli_terminal.py tests/test_cli_commands.py tests/test_import_boundaries.py -q

可接受结果是这些测试全部通过。若存在与本计划无关的预存失败，记录失败测试名、失败原因和确认方式，不要通过修改无关代码掩盖。

手动验收通过标准是：用户在 `/resume` 选择器中按 Enter 后直接回到主 REPL；历史消息已出现在主 scrollback；历史 user 行反色；工具结果显示与正常会话一致；底部输入框可立即继续输入；新输入进入恢复后的 session transcript。

失败标准包括：恢复后进入 page 需要 Esc 返回；恢复历史只显示摘要；工具结果使用恢复专用格式；历史 user 行不是反色；直接 `/resume <target>` 和交互式 `/resume` 行为不一致；恢复后新输入进入了旧 session 或新 session。

## Idempotence and Recovery

本计划的实现步骤可以重复执行。每个 milestone 后先运行聚焦测试，再继续下一步。如果某一步失败，先运行 `git status --short` 查看实际改动范围，只修本计划相关文件，不使用 `git reset --hard` 或 `git checkout --` 回滚，因为工作树可能包含用户或其他 agent 的改动。

删除旧兼容路径前，必须先让新 `replay_messages` 路径测试通过。删除后如果发现仍有调用方依赖 `render_restored_messages()`，不要恢复旧函数；应把调用方迁移到 `replay_messages_to_static()` 或改为不再展示恢复历史 page。若测试环境捕获不到 Rich ANSI，需要像现有 `tests/test_cli_terminal.py::captured_console` 那样使用 `force_terminal=True` 和显式 `color_system` 绑定静态 console。

如果手动验收发现恢复历史和动态输入框重叠，不要从 replay 函数直接写入 prompt_toolkit 动态区；resume 重放发生在 selector 退出、空闲 prompt 重新显示前，应调整 `_handle_command()` 的顺序，确保备用屏幕关闭后、下一次 `PromptSession.read()` 之前完成静态重放。

## Artifacts and Notes

参考文件和快速定位关键词如下。实现者阅读这些文件时，应学习机制和职责分离，不要照搬 TypeScript/React 代码。

`docs/references/ui/screens/ResumeConversation.tsx` 是最重要的参考入口。应学习“选择器只负责选会话，`onSelect` 立即加载完整会话并切换到 REPL”的机制。快速搜索关键词：`function onSelect`、`loadConversationForResume`、`switchSession`、`restoreAgentFromSession`、`restoreWorktreeForResume`、`setResumeData`、`return <REPL`、`LogSelector`、`loadSameRepoMessageLogsProgressive`、`loadAllProjectsMessageLogsProgressive`。

`docs/references/ui/utils/conversationRecovery.ts` 展示恢复前的数据清洗和完整会话加载。应学习集中入口 `loadConversationForResume()` 的职责：按 source 找到日志、加载 lite log 的完整内容、检查一致性、反序列化消息、处理中断 turn、恢复 skill state，并返回 messages 与 session metadata。快速搜索关键词：`loadConversationForResume`、`deserializeMessagesWithInterruptDetection`、`detectTurnInterruption`、`loadMessagesFromJsonlPath`、`restoreSkillStateFromMessages`、`loadFullLog`、`checkResumeConsistency`。

`docs/references/ui/utils/sessionRestore.ts` 展示选择后的 runtime/session 状态恢复。应学习它把“消息加载”和“运行时状态恢复”分开处理：恢复 session id、metadata、agent、worktree、file history、content replacement 和初始 app state。OneCode 第一版不需要照搬所有 feature flag，但应保持类似分层。快速搜索关键词：`processResumedConversation`、`restoreSessionStateFromLog`、`restoreAgentFromSession`、`restoreWorktreeForResume`、`restoreSessionMetadata`、`adoptResumedSessionFile`、`computeStandaloneAgentContext`、`switchSession`。

`docs/references/ui/utils/sessionStorage.ts` 展示 resume picker 的数据层。应学习 progressive loading、lite metadata enrich、过滤 sidechain/current session、从 transcript chain 构造可恢复会话的思路。OneCode 当前 `.onecode/<session_id>/messages.jsonl` 格式更简单，第一版可继续使用 `list_session_summaries()` 和 `MessageStore.from_transcript()`。快速搜索关键词：`loadSameRepoMessageLogsProgressive`、`loadAllProjectsMessageLogsProgressive`、`enrichLogs`、`isLiteLog`、`loadFullLog`、`getSessionIdFromLog`、`buildConversationChain`、`removeExtraFields`、`isTranscriptMessage`。

`docs/references/ui/screens/REPL.tsx` 展示恢复后的主交互层如何接收初始消息并继续运行。应学习它不是把恢复历史作为单独页面，而是把 `initialMessages` 交给 REPL，让之后的 prompt 继续在同一会话上下文中运行。快速搜索关键词：`initialMessages`、`useLogMessages`、`handlePromptSubmit`、`useQueueProcessor`、`Messages`、`PromptInput`、`onCancel`、`onTurnComplete`。

当前 OneCode 对应文件和关键词如下。`ui/cli/resume.py` 搜索 `restore_runtime_from_target`、`restore_session_state`、`list_session_summaries`。`ui/cli/commands.py` 搜索 `_resume`、`_resolve_resume_argument`、`CommandResult`。`ui/cli/terminal/repl.py` 搜索 `_run_resume_selector`、`_handle_command`、`_reset_prompt_session`、`_drain_queue`。`ui/cli/terminal/static_output.py` 搜索 `print_user_submitted`、`print_assistant_markdown`、`print_tool_result`、`user_reverse_style`。`ui/cli/terminal/output_coordinator.py` 搜索 `flush_ready_checkpoints` 和 `_write_static`，理解正常流式会话如何提交 assistant 和 tool result 到静态区。`ui/cli/tool_renderers.py` 搜索 `render_tool_result` 和 `register_renderer`，理解正常工具结果渲染策略。

## Interfaces and Dependencies

不新增第三方依赖。继续使用 Python 标准库、Rich、prompt_toolkit 和项目现有 services。

计划完成时，`ui/cli/types.py::CommandResult` 应能表达恢复成功后的主屏重放请求。建议字段名如下，具体名称可微调，但语义必须清楚：

    replay_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)

计划完成时，`ui/cli/terminal/transcript_replay.py` 应提供一个主入口：

    def replay_messages_to_static(
        messages: Iterable[dict[str, Any]],
        *,
        brightness: str,
        workspace: Path | None = None,
    ) -> None: ...

这个函数只负责静态区重放，不修改 `MessageStore`，不执行工具，不读取 provider，不写 trace。它消费已经恢复好的 message dict，并通过正常静态输出函数打印。

计划完成时，`ui/cli/commands.py::_resume()` 和 `ui/cli/terminal/repl.py::_run_resume_selector()` 应返回同一语义的 `CommandResult`：`runtime` 是恢复后的 runtime，`renderable` 是 `renderer.render_resume(...)` 产生的一行通知，`presentation` 是 `"inline"`，`replay_messages` 是恢复后的当前消息链。二者不再调用 `renderer.render_restored_messages()`。

计划完成时，`ui/cli/terminal/repl.py::_handle_command()` 应在恢复 runtime 后重置 prompt session，并在下一次读取输入前调用 `replay_messages_to_static(...)`。这一步应发生在 selector 退出后、空闲输入框重新出现前。

计划完成时，`ui/cli/renderer.py` 不再包含恢复专用历史渲染函数。`renderer.render_resume()` 保留，因为它是恢复成功通知；`renderer.render_history()` 可保留，因为它服务诊断视图，不是 resume 主路径。

## Revision Notes

- 2026-06-19 / Codex：创建本 ExecPlan。原因是用户要求参考给定 TypeScript/React resume 机制，为 OneCode 撰写中文计划文档，目标是把 `/resume` 从“历史 page 展示”改为“选中后真正恢复到可继续交互的主 REPL”，并保证恢复历史消息复用正常会话渲染路径。
