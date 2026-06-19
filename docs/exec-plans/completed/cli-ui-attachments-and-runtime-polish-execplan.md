# 修复 CLI 页面退出、附件补全、历史渲染和运行时轮数行为

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文件必须按照仓库根目录的 `PLANS.md` 维护。任何实现者都应把本文当作唯一上下文：即使没有阅读此前对话，也能从当前工作树和本计划完成端到端修复。

## Purpose / Big Picture

完成本计划后，OneCode 的交互式 CLI 会更符合 code-agent 用户的直觉。临时页面只用 `Esc` 返回，不再让 `q` 或 `Enter` 意外关闭；主会话不再因为默认 20 轮上限停止；恢复历史里的用户输入会明确反色；`@file` 附件补全会先补全路径并追加空格，而不会按 `Enter` 直接发送；正常输入框不再常驻补全提示；工具结果会以稳定、短小的一行摘要显示；附件投影不会再触发 thinking/reasoning provider 对真实 assistant 消息的校验错误。

实现完成后，从 `D:\study\OneCode` 启动 `uv run python -m ui.cli.app` 可以观察：输入 `/usage` 后只有 `Esc` 返回；连续长工具回合不会因 20 轮停止；执行 `/resume` 选择会话后，历史 user 行反色；输入 `@ui/cli/ren` 后按 `Enter` 或 `Tab` 只补成 `@ui/cli/renderer.py `，不会发送；普通空输入框底部没有 `Enter to send...` 固定提示；工具调用结果类似 `[read_file] Read 82 line(s) from ui/cli/renderer.py`。

## Progress

- [x] (2026-06-14 13:35+08:00) 阅读 `PLANS.md`，确认 ExecPlan 必须自包含、包含 living sections，且写入 `.md` 文件时不需要外层三反引号。
- [x] (2026-06-14 13:40+08:00) 阅读并定位当前 CLI、runtime、附件、provider 和工具摘要相关代码路径。
- [x] (2026-06-14 13:48+08:00) 阅读参考文件 `docs/references/ui/utils/messages.ts` 与 `docs/references/attachement/attachments.ts`，校准附件与 API 归一化思路。
- [x] (2026-06-14 14:00+08:00) 创建本中文 ExecPlan，覆盖用户提出的六类行为修复。
- [x] (2026-06-14 18:20+08:00) 实现页面退出键修复：临时 page 去掉 `q` 和 `Enter` 关闭，只保留 `Esc` 与必要的 `Ctrl-C`。
- [x] (2026-06-14 18:20+08:00) 实现主会话无限 turn 行为，同时保留子 agent 和后台 memory job 的防失控轮数。
- [x] (2026-06-14 18:20+08:00) 实现恢复历史 user 行稳定反色，并补充备用屏幕渲染回归测试。
- [x] (2026-06-14 18:20+08:00) 实现附件补全按类型分流、补全后追加空格、普通输入时隐藏底部提示。
- [x] (2026-06-14 18:20+08:00) 实现附件来源消息的 provider-safe 投影或归一化，避免 `reasoning_content` / thinking 校验误伤 synthetic attachment。
- [x] (2026-06-14 18:20+08:00) 实现工具结果摘要文案统一，并用示例字符串补测试。
- [x] (2026-06-14 18:25+08:00) 运行聚焦测试和 compile 检查；全量测试仍有 2 个与本计划无关的既有断言失败，见 Outcomes。
- [ ] 完成真实 TTY 手动 CLI 验收后，更新本计划并把文件移动到 `docs/exec-plans/completed/`。

## Surprises & Discoveries

- Observation: `docs/exec-plans/active/` 在当前工作树中一度不存在，`git status --short` 显示旧 active 计划被删除，并有 `docs/exec-plans/completed/active/` 未跟踪目录。
  Evidence: `git status --short` 输出包含 `D docs/exec-plans/active/cli-input-control-and-completion-execplan.md` 与 `?? docs/exec-plans/completed/active/`。本计划不回退这些已有状态，只按仓库约定重新创建 active 目录并新增本文件。

- Observation: `/usage`、`/status` 等 page 之所以能用 `q` 退出，是通用 `TransientPage` 显式绑定了 `Esc`、`q`、`Enter` 和 `Ctrl-C` 到同一个 `_close()`。
  Evidence: `ui/cli/terminal/page.py` 中 `@bindings.add(Keys.Escape, eager=True)` 后连续绑定 `@bindings.add("q", eager=True)`、`@bindings.add(Keys.Enter, eager=True)`、`@bindings.add(Keys.ControlC, eager=True)`。footer 文案也写着 `Esc/q to return`。

- Observation: `/resume` 的选择器本身没有绑定 `q`；只有选择后显示的恢复摘要 page 继承了 `TransientPage` 的 `q` 关闭行为。
  Evidence: `ui/cli/terminal/selector.py` 的 `TransientSelector` 只绑定 Down、Up、Enter、Esc 和 Ctrl-C。

- Observation: 主会话 20 轮上限来自 `RuntimeState.max_turns: int = 20`，loop 每轮先递增 `turn_count`，再检查 `turn_count > max_turns`。
  Evidence: `core/runtime_state.py` 定义默认值 20；`core/loop.py` 在 `_run_loop_async()` 中检查 `if self.state.turn_count > self.state.max_turns:` 并返回 `Stopped: maximum turn count reached.`。

- Observation: 恢复历史 user 行当前已经用 `style="reverse"`，但主屏 user 行使用显式 `white on black` 或 `black on white`。备用屏幕经过 Rich 到 ANSI 再到 prompt_toolkit 的转换时，抽象 `reverse` 可能不如显式前景/背景可靠。
  Evidence: `ui/cli/renderer.py::_restored_message_line()` 对 user 返回 `Text(..., style="reverse")`；`ui/cli/terminal/static_output.py::user_reverse_style()` 对 light/dark 终端返回显式反色样式。

- Observation: 当前 `@file` 附件投影为 synthetic assistant `read_file` tool call 加 synthetic `tool_result`，这会把附件内容放入一段看起来像 assistant 工具调用历史的消息链。
  Evidence: `services/attachments/projector.py::_project_file()` 返回 `role="assistant"` 且含 `tool_calls`，随后返回 `role="tool_result"`。这条 assistant 消息带 `metadata: {"synthetic": True, "source": "attachment"}`，但 provider adapter 当前没有使用该 metadata 做 thinking/reasoning 区分。

- Observation: 参考实现没有简单把附件当普通文本；它保留 attachment 类型，在 `normalizeMessagesForAPI()` 中统一把 attachment 转成 API 消息，并在同一流程中处理 thinking-only orphan、trailing thinking、tool_result pairing、附件冒泡和 user message 合并。
  Evidence: `docs/references/ui/utils/messages.ts` 中 `normalizeMessagesForAPI()` 先调用 `reorderAttachmentsForAPI()`，在 `case 'attachment'` 中调用 `normalizeAttachmentForAPI()`，随后调用 `filterOrphanedThinkingOnlyMessages()`、`filterTrailingThinkingFromLastAssistant()`、`ensureNonEmptyAssistantContent()` 与 `ensureToolResultPairing()`。

- Observation: 参考实现的 file attachment 在 API 归一化阶段会生成类似工具调用事实的内容，但不是把未治理的 raw attachment 直接交给 provider。
  Evidence: `docs/references/ui/utils/messages.ts` 的 `normalizeAttachmentForAPI()` 在 `case 'file'` 中调用 `createToolUseMessage(FileReadTool.name, ...)` 与 `createToolResultMessage(FileReadTool, fileContent)`；这些 helper 最终生成 meta user messages，例如 `Called the ... tool...` 与 `Result of calling the ... tool...`，而不是 provider wire 层真实 assistant tool call。

## Decision Log

- Decision: `TransientPage` 的返回键只保留 `Esc`，并保留 `Ctrl-C` 作为通用中断兜底；删除 `q` 和 `Enter` 关闭。
  Rationale: 用户明确期望 `resume`、`usage` 等页面不应由 `q` 退出。`Enter` 在 page 中没有“确认”语义，保留会增加误关闭。`Ctrl-C` 作为终端级退出兜底可继续存在，但 footer 只宣传 `Esc`。
  Date/Author: 2026-06-14 / Codex

- Decision: 主交互 runtime 的 `max_turns` 使用 `None` 表示无限，而不是使用一个很大的整数。
  Rationale: 很大的整数仍然是假上限，会污染 `/status` 和 trace 语义。`None` 明确表达“没有上限”。子 agent、memory extraction 和其他自动后台流程仍应保留独立上限，避免无人值守任务失控。
  Date/Author: 2026-06-14 / Codex

- Decision: 恢复历史 user 行复用主屏显式反色样式，而不是继续使用 Rich 的 `reverse` 关键字。
  Rationale: 主屏已经按终端亮暗选择明确的前景/背景。恢复历史应和主屏视觉一致，并减少备用屏幕 ANSI 转换差异。
  Date/Author: 2026-06-14 / Codex

- Decision: 附件导致的 `reasoning_content` 问题通过区分 attachment-origin synthetic messages 与真实 provider assistant messages 解决，不做全局“跳过校验”。
  Rationale: reasoning/thinking 校验对真实 assistant 历史有价值，不能整体绕过。参考实现说明附件应在 API 归一化边界被治理；OneCode 应补足这一边界，避免 synthetic attachment 被误当成真实 assistant response。
  Date/Author: 2026-06-14 / Codex

- Decision: `@file` 补全的 `Enter` 与 `Tab` 都只接受补全并追加空格，不提交；slash command completion 的 `Enter` 继续接受并提交。
  Rationale: 文件补全是编辑输入的一部分，用户通常还要继续写问题。slash command 是完整命令入口，`Enter` 执行符合当前 CLI 行为和已有测试意图。
  Date/Author: 2026-06-14 / Codex

- Decision: 工具结果摘要继续由 `ui/cli/tool_renderers.py` 统一生成，不把文案散落到 `stream_session.py` 或 `static_output.py`。
  Rationale: `tool_renderers.py` 已是工具结果一行摘要的唯一合适边界。stream/static 层只负责把摘要写到终端，不应理解每个工具的 metadata。
  Date/Author: 2026-06-14 / Codex

## Outcomes & Retrospective

已实现代码修复。实际修改覆盖 `ui/cli/terminal/page.py`、`core/runtime_state.py`、`core/loop.py`、`ui/cli/views/status.py`、`ui/cli/renderer.py`、`ui/cli/terminal/repl.py`、`ui/cli/terminal/completer.py`、`ui/cli/terminal/prompt_session.py`、`services/attachments/projector.py`、`ui/cli/tool_renderers.py` 及对应测试。

已验证通过：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py -q
    uv run python -m pytest tests/test_loop.py -q
    uv run python -m pytest tests/test_attachment_runtime.py tests/test_attachment_projector.py tests/test_openai_compatible_provider.py -q
    uv run python -m pytest tests/test_cli_tool_renderers.py -q
    uv run python -m compileall core services infrastructure ui

全量 `uv run python -m pytest tests -q` 结果为 417 passed、2 failed。剩余失败与本计划改动无关，且 `git diff` 未触碰相关工具代码：`tests/test_bash_tool.py::test_bash_descriptor_schema_and_prompt` 仍期待 bash prompt 包含 `Tree-sitter`；`tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts` 仍期待 search tool prompt 以 `glob:` / `grep:` 开头，但当前 prompt 文本以 `Purpose:` 开头。

尚未执行真实 TTY 手动 CLI 验收，因此本计划仍留在 `docs/exec-plans/active/`，未移动到 completed。

## Context and Orientation

OneCode 是一个 Python code agent runtime。交互式 CLI 位于 `ui/cli/`，它负责用户输入、命令页面、补全菜单、流式输出和工具结果展示；agent 主循环位于 `core/loop.py`，它负责每轮调用模型、执行工具和决定是否继续。附件系统位于 `services/attachments/`，它在用户输入中解析 `@file` 等 mention，收集附件，并在模型调用前把内部 `role="attachment"` 消息投影成 provider 可见消息。OpenAI-compatible provider adapter 位于 `infrastructure/providers/chat_completions.py`，它把 OneCode 内部 message shape 转成 Chat Completions HTTP payload。

本计划使用几个术语。`TransientPage` 指 `ui/cli/terminal/page.py` 中的全屏临时页面，用于 `/usage`、`/status` 和恢复摘要；它进入备用屏幕，退出后不污染主终端 scrollback。`TransientSelector` 指 `ui/cli/terminal/selector.py` 中的全屏选择列表，用于 `/resume` 会话选择。`RuntimeState` 指 `core/runtime_state.py` 中单个会话的可变状态，包括 token usage、turn count 和 session id。这里的 turn 是 agent 主循环的一次模型调用和可能的工具执行，不等同于用户发出的自然语言消息。`attachment-origin synthetic message` 指由附件系统生成的模型可见消息，它不是 provider 返回的真实 assistant response，但可能包含和工具调用相似的内容。

当前相关文件如下。`ui/cli/terminal/page.py` 管 page 的按键绑定和 footer；`ui/cli/terminal/selector.py` 管 `/resume` 选择器；`ui/cli/terminal/prompt_session.py` 管输入框、补全、Enter/Tab 行为和底部提示；`ui/cli/terminal/static_output.py` 管主屏 user 行反色；`ui/cli/renderer.py` 管恢复历史和命令输出 renderable；`ui/cli/views/status.py` 管 `/status` 与 `/usage` 页面；`ui/cli/tool_renderers.py` 管工具结果摘要；`core/runtime_state.py` 与 `core/loop.py` 管 turn 上限；`services/attachments/projector.py` 管 attachment 到 provider-visible messages 的投影；`infrastructure/providers/chat_completions.py` 管 final HTTP payload。

参考文件放在 `docs/references/`，不是直接参与运行的代码，但它们说明了目标产品思路。`docs/references/attachement/attachments.ts` 展示附件先作为 typed attachment 被收集，例如 `file`、`already_read_file`、`directory`。`docs/references/ui/utils/messages.ts` 展示附件在 API 归一化阶段被转换，并和 thinking、tool_result pairing、消息合并等规则一起治理。OneCode 不需要逐字照搬 TypeScript，但应采用同一个边界原则：附件是内部事实，provider payload 是经过投影和校验后的结果。

## Plan of Work

第一步修复临时页面退出键。编辑 `ui/cli/terminal/page.py` 的 `TransientPage.show()`。删除 `_close()` 上的 `@bindings.add("q", eager=True)` 和 `@bindings.add(Keys.Enter, eager=True)`，保留 `Keys.Escape` 和 `Keys.ControlC`。更新 `footer_text()`，把 `Esc/q to return · ↑↓ to scroll` 改成 `Esc to return · ↑↓ to scroll`。随后检查 `ui/cli/terminal/selector.py`，确认不需要改动，因为它没有 `q` 绑定，footer 已经写 `Enter to select · Esc to cancel`。

第二步把主会话 turn 上限改为无限。编辑 `core/runtime_state.py`，把 `max_turns` 类型改为 `int | None`，默认值改为 `None`。`start_new_session()` 的注释需要同步：`max_turns` 仍是配置值，但 `None` 表示无限。编辑 `core/loop.py`，把检查改成 `if self.state.max_turns is not None and self.state.turn_count > self.state.max_turns:`。保留 `TransitionReason.MAX_TURNS`，因为子 agent 或测试仍可能显式设置有限上限。编辑 `services/subagents/runner.py` 时要谨慎：内置 subagent 的默认 `20` 是子任务安全边界，除非某个子 agent definition 明确要求无限，否则不要删除。若 `RuntimeState(max_turns=runtime.state.max_turns)` 用于恢复主 runtime，则它会自然继承 `None`。

第三步调整 `/status` 与 `/usage` 展示。编辑 `ui/cli/views/status.py`。新增一个小 helper，例如 `_turns_summary(runtime)`，当 `runtime.state.max_turns is None` 时返回 `str(runtime.state.turn_count)` 或 `f"{runtime.state.turn_count}/unlimited"`。`/status` 可以保留 turns 行，但应该显示 `3/unlimited`。`/usage` 是 token usage 页面，建议删除 `turns` 行，避免把 runtime loop count 混入 token usage；如果团队希望保留，也必须显示 `unlimited` 而不是 `20`。本计划选择从 `/usage` 删除 turns 行，把 turns 只留在 `/status`。

第四步修复恢复历史 user 反色。编辑 `ui/cli/renderer.py`，让 `render_restored_messages()` 接收可选参数 `brightness: str = "dark"`，并在 user 分支使用 `ui.cli.terminal.static_output.user_reverse_style(brightness)`，不要再使用 `style="reverse"`。然后更新调用点：`ui/cli/terminal/repl.py::_run_resume_selector()` 调用 `renderer.render_restored_messages(..., brightness=self._brightness)`；`ui/cli/commands.py` 中处理 `/resume <target>` 的直接恢复路径目前没有 `InlineRepl` 的 brightness。为避免 command registry 依赖 terminal brightness，可以让直接命令路径仍使用默认 dark，或把 brightness 作为 CLI 层包装 renderable 的责任。更稳妥的做法是给 `CommandResult` 不新增字段，先让 `render_restored_messages()` 默认 dark；在 TTY selector 路径传真实 brightness。测试应覆盖 helper 输出 style，不依赖真实终端颜色。

第五步修复补全行为和底部提示。编辑 `ui/cli/terminal/completer.py`，创建 `Completion` 时把原始 `SuggestionItem` 或至少 `item.kind` 放入 completion 的 `data` 字段。编辑 `ui/cli/terminal/prompt_session.py`，添加 helper `_completion_kind(completion) -> str | None` 与 `_apply_completion_for_edit(buffer, completion) -> None`。`Enter` 处理逻辑改为：如果 completion kind 是 `file` 或 `directory`，应用补全，文件补全后追加一个空格，目录补全保持尾部 `/` 以便继续选择下一级，然后不提交；如果 kind 是 `command` 或 `session`，保持当前接受并提交行为；如果没有 completion，按原普通提交行为。`Tab` 处理逻辑改为：任何 completion 都只应用不提交；对 `file` 追加空格，对 `directory` 不追加空格。底部 hint 改为默认隐藏：`_hint_window()` 的 Condition 只在 hint 文本非空、或 suggestion rows 非空、或 Ctrl-C 二次退出提示激活时显示。默认 `bottom_hint` 改成空字符串；当 `_suggestion_rows(buffer)` 非空时显示 `Enter to accept · Tab to fill · ↑↓ to choose`。不要在普通空闲输入时显示 `Enter to send · Tab to fill · ↑↓ to choose · Ctrl-C to cancel`。

第六步修复附件和 provider reasoning/thinking 边界。先添加测试来锁定当前失败形状：构造一个 message chain，包含真实 assistant 消息可能带 provider-specific `reasoning_content`，再包含 `attachment` file，经 context preparer 和 chat completions payload builder 后，断言 attachment-origin synthetic 不会被当作真实 assistant reasoning 消息要求处理。具体实现有两种可接受路径，执行者应优先选择更符合 OneCode 当前代码的最小改动。

首选路径是修改 `services/attachments/projector.py::_project_file()`，不再返回 provider-visible `role="assistant"` + `role="tool_result"` pair，而返回一条或多条 synthetic `role="user"` message，内容表达为 `Called the read_file tool with...` 与 `Result of calling the read_file tool...`，并带 metadata `synthetic=True, source=attachment, attachment_type=file`。这贴近参考实现的 `createToolUseMessage()` 与 `createToolResultMessage()`：它保留“这个内容来自读取文件”的语义，但不会生成真实 assistant tool call，所以不会触发 OpenAI-compatible provider 对 assistant reasoning content 的校验。对于大文件截断，追加一条 meta user notice，说明文件已截断并建议必要时用 read_file 继续读取。

备选路径是保留 synthetic assistant/tool_result pair，但在 `infrastructure/providers/chat_completions.py::_project_messages()` 中识别 `message.metadata.synthetic is True and source == "attachment"`，将这组 pair 转成 user-side context 文本，或者至少剥离/转换为 provider 不会当成真实 assistant response 的结构。这个路径改动集中在 provider adapter，但会让 attachment projection 和 provider projection 之间承担重复语义。除非首选路径破坏现有测试太多，否则不要选备选路径。

无论选择哪条路径，都不要全局吞掉 provider 的 `reasoning_content` 错误。真实 provider assistant 消息如果包含需要回传的 reasoning 字段，应在 future provider adapter 中保留或正确剥离；attachment synthetic 消息不应伪装成真实 assistant。补充测试时，至少覆盖：file attachment 不产生 provider payload 里的 synthetic assistant tool_calls；raw internal `role="attachment"` 不进入 provider payload；普通真实 assistant tool_calls 仍能投影为 assistant + tool wire format。

第七步统一工具结果摘要。编辑 `ui/cli/tool_renderers.py`。保留 `render_tool_result(result, workspace=workspace)` 为唯一入口。新增 `_plural(count, singular, plural=None)` helper，用于 `1 file` 和 `2 files`，但根据用户示例 `line(s)` 与 `replacement(s)` 可以保留括号式文案在 read/write/edit 中。把 grep content/count 的成功摘要统一为 `[grep] Found N matches across M files`；如果 metadata 只提供 `num_lines`，把它视作 matches 数量，避免输出 `line(s)`。glob 成功摘要统一为 `[glob] Found TOTAL files, showing SHOWN`，如果没有分页则输出 `[glob] Found TOTAL files`。bash 保持 `[bash error] exit CODE in D ms, stdout N chars, stderr M chars`。write/edit/read 保持用户给出的格式。fallback 仍保留 `[tool] call id`，供未知 MCP/插件工具使用。

第八步更新测试。`tests/test_cli_terminal.py` 或现有 page 测试中增加断言：`q` 和 `Enter` 不关闭 `TransientPage`，`Esc` 关闭；footer 不包含 `q`。`tests/test_loop.py` 中新增默认无限 turns 测试：用一个会持续产生 tool call 的 fake model 跑超过 20 次，确认不会触发 `MAX_TURNS`；保留显式 `max_turns=1` 的旧测试，确认有限上限仍生效。`tests/test_cli_prompt_input_suggestions.py` 或 prompt session 测试中新增：`@file` 按 Enter 只补全并追加空格，不返回 SUBMIT；`/resume` suggestion 按 Enter 仍 SUBMIT；普通输入无 suggestion 时 hint 为空。`tests/test_attachment_projector.py` 与 `tests/test_openai_compatible_provider.py` 中新增 attachment projection/provider payload 测试。`tests/test_cli_tool_renderers.py` 或现有工具 renderer 测试中新增用户给出的六条摘要字符串。

## Concrete Steps

从仓库根目录工作：

    cd D:\study\OneCode

先查看当前工作树，确认没有误改用户文件：

    git status --short

定位要修改的代码：

    rg -n "bindings.add\\(\"q\"|Esc/q|max_turns|turn_count|render_usage|render_restored_messages|bottom_hint|Completion\\(|_project_file|render_tool_result" ui core services infrastructure tests

按 Plan of Work 的顺序小步修改。每完成一个小步，运行对应聚焦测试。建议测试顺序如下：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py -q
    uv run python -m pytest tests/test_loop.py -q
    uv run python -m pytest tests/test_attachment_projector.py tests/test_openai_compatible_provider.py -q
    uv run python -m pytest tests/test_cli_tool_renderers.py -q

如果某个测试文件当前不存在，不要跳过覆盖；在 `tests/` 下创建最贴近现有命名的新测试文件，并把命令改为包含新文件。例如工具摘要没有专门测试文件时，创建 `tests/test_cli_tool_renderers.py`。

最后运行更宽的验证：

    uv run python -m compileall core services infrastructure ui
    uv run python -m pytest tests -q

手动验收时启动 CLI：

    uv run python -m ui.cli.app

手动观察应符合：

    输入 /usage 后，按 q 不退出，按 Enter 不退出，按 Esc 返回主输入框。
    输入 /status 后，turns 显示为 0/unlimited 或当前计数/unlimited。
    输入 /usage 后，只显示 token 和 compaction usage，不显示 turns。
    输入 /resume 选择一个会话后，恢复摘要里的 > user 历史行有明确反色。
    在输入框键入 @ui/cli/ren 后，按 Tab 或 Enter 补全为 @ui/cli/renderer.py 后跟一个空格，不发送。
    普通空输入框底部不显示 Enter to send · Tab to fill · ↑↓ to choose · Ctrl-C to cancel。
    触发 read_file、grep、glob、bash error、write_file、edit_file 后，静态区结果摘要匹配用户示例风格。

## Validation and Acceptance

自动化验收必须证明行为变化，而不只是代码编译。运行：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py tests/test_loop.py tests/test_attachment_projector.py tests/test_openai_compatible_provider.py tests/test_cli_tool_renderers.py -q

预期这些聚焦测试全部通过。新增测试应在实现前失败，失败原因分别对应：`q`/`Enter` 关闭 page、默认 max turns 仍为 20、恢复 user 行只用 `reverse`、`@file` Enter 直接提交、普通 hint 常驻、file attachment 产生 synthetic assistant tool call、工具摘要文案不匹配。

运行：

    uv run python -m compileall core services infrastructure ui

预期没有语法错误。运行：

    uv run python -m pytest tests -q

预期全量测试通过。如果全量测试耗时或受本地 provider 配置影响而失败，必须在 Outcomes 中记录失败测试名、失败原因、是否与本计划有关，并至少保证聚焦测试和 compile 通过。

手动验收必须覆盖真实 TTY，因为 prompt_toolkit 的按键、备用屏幕和颜色在 headless 测试中不能完全证明。启动 `uv run python -m ui.cli.app` 后，依次验证 `/usage` 键位、`@file` 补全、普通 hint 隐藏、`/resume` 历史反色和工具摘要。若本机没有 provider API key，仍可验证 `/usage`、补全和 `/resume`；工具摘要可通过测试证明，或使用 fake provider/integration harness。

## Idempotence and Recovery

本计划的代码改动应是幂等的：重复运行测试不会改变源文件；手动 CLI 会按正常运行创建或更新 `.onecode/<session_id>/`，这是 runtime artifact，不应纳入源码提交。不要删除 `.onecode` 或用户已有 session。不要回退当前工作树里他人已有的删除或移动，例如已存在的 `docs/exec-plans/completed/active/` 状态；如果这些状态影响提交，应在最终汇报中说明，而不是自行 reset。

如果修改 `RuntimeState.max_turns` 后出现类型错误，优先修正显式构造点，让主 runtime 传 `None`，子 agent 传整数。不要用 `999999` 代替无限。如果附件投影改动导致旧测试期待 assistant/tool_result pair 失败，应更新测试以反映新边界，并保留一条测试证明普通真实 tool call 仍按 assistant/tool wire format 投影。

如果真实 provider 仍返回 `reasoning_content` 错误，记录 HTTP error body 和最终 payload 的 message role 摘要到 Outcomes，但不要输出 API key。下一步应在 provider adapter 中保留或清理真实 assistant reasoning fields，而不是回退附件投影边界。

## Artifacts and Notes

当前代码证据摘录：

    ui/cli/terminal/page.py:
      @bindings.add(Keys.Escape, eager=True)
      @bindings.add("q", eager=True)
      @bindings.add(Keys.Enter, eager=True)
      @bindings.add(Keys.ControlC, eager=True)
      def _close(event): event.app.exit()

    core/runtime_state.py:
      max_turns: int = 20

    core/loop.py:
      self.state.turn_count += 1
      if self.state.turn_count > self.state.max_turns:
          self.state.set_transition(TransitionReason.MAX_TURNS)

    ui/cli/renderer.py:
      if role == "user":
          return Text(f"> {preview(message.get('content'))}", style="reverse")

    services/attachments/projector.py:
      file attachment returns a synthetic assistant message with read_file tool_calls and a synthetic tool_result.

参考实现要点摘录：

    docs/references/ui/utils/messages.ts:
      normalizeMessagesForAPI() handles attachment messages in case 'attachment',
      then filters orphaned thinking-only messages, trailing thinking, whitespace-only assistant messages,
      and validates tool_use/tool_result pairing.

    docs/references/ui/utils/messages.ts:
      normalizeAttachmentForAPI() case 'file' creates createToolUseMessage(FileReadTool.name, ...)
      and createToolResultMessage(FileReadTool, fileContent). These helpers create meta user messages,
      not raw provider assistant messages.

    docs/references/attachement/attachments.ts:
      generateFileAttachment() returns typed attachment records such as file, already_read_file,
      compact_file_reference, and pdf_reference after permission and file-state checks.

目标工具摘要示例：

    [read_file] Read 82 line(s) from ui/cli/renderer.py
    [grep] Found 6 matches across 2 files
    [glob] Found 31 files, showing 10
    [bash error] exit 1 in 142 ms, stdout 0 chars, stderr 230 chars
    [write_file] Updated docs/design-docs/example.md (24 line(s), diff truncated)
    [edit_file] Edited ui/cli/renderer.py with 1 replacement(s)

## Interfaces and Dependencies

不要引入新的第三方依赖。继续使用现有 `prompt_toolkit` 和 Rich。CLI 相关改动应留在 `ui/cli/`，runtime turn 上限改动留在 `core/` 和必要的 subagent 构造点，附件投影改动留在 `services/attachments/` 和必要的 provider adapter 测试中。

实现后应保持或新增以下接口语义：

    core.runtime_state.RuntimeState.max_turns: int | None
      None means unlimited for the current runtime.
      A positive integer means stop after that many loop turns.

    ui.cli.renderer.render_restored_messages(messages, *, brightness: str = "dark") -> Group
      User messages render with the same explicit reverse-video style as submitted user lines.

    ui.cli.terminal.prompt_session.PromptSession.read(...) -> PromptSubmission
      Enter on command/session completion accepts and submits.
      Enter on file completion accepts, appends a space for files, and keeps editing.
      Tab on any completion accepts but never submits.

    services.attachments.projector.AttachmentProjector.project(messages, state) -> tuple[dict, ...]
      Raw role="attachment" never reaches provider payload.
      File attachments become provider-safe synthetic user context or another shape that cannot be mistaken for real assistant reasoning output.

    ui.cli.tool_renderers.render_tool_result(result, *, workspace) -> str
      Returns one concise line for known built-in tools and a stable fallback for unknown tools.

Revision note, 2026-06-14 / Codex: 初始中文 ExecPlan 创建。计划吸收了用户对 CLI 行为、附件 reasoning 错误、补全语义和工具摘要格式的要求，并根据 `docs/references/ui/utils/messages.ts` 与 `docs/references/attachement/attachments.ts` 调整附件方案：在 API 投影边界治理 attachment-origin synthetic messages，而不是全局跳过 provider 校验。
