# 实现完整计划模式

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。

本仓库的 ExecPlan 规范见仓库根目录 `PLANS.md`。本文档按 `PLANS.md` 维护：它必须自包含、面向没有上下文的新手、以可观察行为验收，并记录实现期间的决策和发现。


## Purpose / Big Picture

实现完成后，OneCode 用户可以在 CLI 中输入 `/plan` 进入正式计划模式。计划模式会让 agent 在写业务代码前先只读探索代码库、把计划写入 `.onecode/plans/` 下的 Markdown 文件、用结构化问题与用户面试式澄清需求，并在调用 `exit_plan_mode` 后把计划提交给用户审批。只有用户批准后，OneCode 才恢复进入计划模式前的权限模式并开始实施。

这不是临时 MVP，而是一次重构性质的完整实现。计划模式会成为 OneCode 的一等运行时模式：核心状态、权限裁剪、工具可见性、计划文件存储、附件投影、CLI `/plan` 命令、计划审批 UI、只读 explore subagent 和冲突感知并发调度都围绕同一套正式契约实现。旧的 plan-mode 占位 attachment 和任何隐式 `metadata` 协议要被删除或替换，不保留迁移式兼容路径。


## Progress

- [x] (2026-06-22 21:45+08:00) 已阅读 `PLANS.md`、`architecture.md`、核心设计文档、现有 CLI/权限/prompt/工具运行时代码，以及用户指定的 Claude Code 参考目录。
- [x] (2026-06-22 22:05+08:00) 已根据用户确认的完整范围创建本 ExecPlan，尚未修改 runtime 实现代码。
- [x] (2026-06-22 22:30+08:00) 阶段 1：在 `core/runtime_state.py` 新增 `PermissionMode` 与 `PlanState`，删除 metadata 隐式协议。
- [x] (2026-06-22 22:40+08:00) 阶段 2：新增 `services/plans/`（store/transitions/prompts/attachments/injection），把 `.onecode/plans/` 路径管理、fork 复制、resume 恢复和 attachment 注入接入 runtime。
- [x] (2026-06-22 22:50+08:00) 阶段 3：新增 `tools/enter_plan_mode`、`tools/exit_plan_mode`、`tools/ask_user_question` 三个工具，并新增 `services/questions/` 协议。
- [x] (2026-06-22 23:00+08:00) 阶段 4：在 `services/permissions/policy.py` 加入 plan-mode 白名单与“只能写计划文件”的硬约束，扩展 `is_tool_visible`。
- [x] (2026-06-22 23:05+08:00) 阶段 5：`services/attachments/projector.py` 替换旧的 `[plan mode attachment]` 占位，引入 intro/reentry/exit 三种 variant。
- [x] (2026-06-22 23:10+08:00) 阶段 6：`services/plans/injection.py` 在 CLI 调用 model 前把 plan attachment 注入 message_store。
- [x] (2026-06-22 23:15+08:00) 阶段 7：在 `ui/cli/commands.py` 注册 `/plan`、`/plan show|open|approve|reject` 子命令。
- [x] (2026-06-22 23:20+08:00) 阶段 8：`ui/cli/types.py` 增加 `plan_store` 与 `user_question_prompter`，`ui/cli/app.py` 在 build_runtime 中创建并注入 PlanStore。
- [x] (2026-06-22 23:25+08:00) 阶段 9：`tools/agent/tool.py` 支持 `focus_paths`，`services/subagents/runner.py` 让 `subagent_type="explore"` 强制 read_only 并隐藏 agent 工具。
- [x] (2026-06-22 23:30+08:00) 阶段 10：新增 `services/tools/conflicts.py`，`executor.py` 改为基于 `ToolTarget` 的目标冲突感知批调度。
- [x] (2026-06-22 23:40+08:00) 阶段 11：更新 `tests/test_attachment_projector.py` 旧占位，新增 `tests/test_plan_mode.py`（27 用例）与 `tests/test_tool_conflicts.py`（10 用例），扩展 `tests/test_import_boundaries.py`。运行 `uv run python -m pytest tests -q --ignore=tests/test_bash_tool.py --ignore=tests/test_openai_compatible_provider.py --ignore=tests/test_search_tools.py` 580 passed。Pre-existing 失败与本计划无关（baseline 即如此）。
- [x] (2026-06-22 23:55+08:00) 修复 `/plan` CLI 集成缺口：默认 `/plan` 不再打开 transient page，而是 inline 显示 enabled；REPL 现在消费 `queued_prompt` 与 plan attachments，并在下一轮 agent turn 前注入 plan-mode attachment；空闲输入框会显示 `plan mode on`。
- [x] (2026-06-23 00:05+08:00) 按用户要求删除 plan 专用备用屏幕/面板兼容代码：`/plan show` 改为 inline 输出，移除 `_render_plan_panel()`，不保留旧 page panel 路径。


## Surprises & Discoveries

- Observation: 当前 `services/attachments/projector.py` 已有 `plan_mode` attachment 分支，但它只是把 `content` 包进 `[plan mode attachment]`，没有计划模式语义。
  Evidence: 搜索 `rg -n "plan_mode" services tests` 可看到 `tests/test_attachment_projector.py` 只断言 raw attachment role 不暴露给 provider，没有断言任何计划工作流。

- Observation: OneCode 已有 `read_only_agent` 硬限制和 subagent runner，适合作为 explore agent 只读执行的基础，但当前该状态主要通过 `RuntimeState.metadata` 表达。
  Evidence: 搜索 `rg -n "read_only_agent" services tools tests` 可看到 `services/permissions/policy.py` 会拒绝只读 agent 的非只读或文件修改工具调用。

- Observation: Claude Code 的计划模式不是普通 prompt，而是由 `EnterPlanMode`、`ExitPlanMode`、`AskUserQuestion`、计划文件、permission mode、系统 attachment 和 TUI 审批共同构成的状态机。
  Evidence: 参考 `docs/references/Tools_full/EnterPlanModeTool/EnterPlanModeTool.ts`、`docs/references/Tools_full/ExitPlanModeTool/ExitPlanModeV2Tool.ts`、`docs/references/Tools_full/AskUserQuestionTool/AskUserQuestionTool.tsx` 和 `docs/references/ui/utils/messages.ts`。

- Observation: `/plan` 命令虽然会设置 `RuntimeState.permission_mode = PLAN`，但此前返回 `presentation="page"`，导致用户进入一个显示计划状态的临时页面；同时 `CommandResult.queued_prompt` 和 `CommandResult.attachments` 没有被 `InlineRepl` 消费，导致后续 agent turn 没有自动获得 plan-mode attachment。
  Evidence: `ui/cli/commands.py::_plan()` 返回 page panel；`ui/cli/terminal/repl.py::_handle_command()` 只处理 renderable/replay/exit，不处理 queued prompt 或 attachments。


## Decision Log

- Decision: 计划模式是正式 `PermissionMode.PLAN`，并在 `RuntimeState` 上新增结构化 plan state，不使用 `RuntimeState.metadata` 存储核心计划生命周期。
  Rationale: 用户明确指出，如果计划模式是核心能力，就不应以裸 metadata 作为隐式协议。结构化 state 能让权限、工具可见性、prompt、CLI 和测试共享同一个事实来源。
  Date/Author: 2026-06-22 / Codex

- Decision: 计划文件放在 workspace 的 `.onecode/plans/`，不放 session 目录。
  Rationale: 用户明确要求放在 `.onecode` 下的 `plans` 文件夹。该位置也与 Claude Code `utils/plans.ts` 中“plans directory + session slug cache”的机制更接近，便于跨 session resume 和 fork 复制。
  Date/Author: 2026-06-22 / Codex

- Decision: 不新增计划写工具，计划文件由现有 `write_file` 和 `edit_file` 写入。
  Rationale: 用户明确要求没有必要新增计划写工具。权限层负责在 plan mode 下只允许通用文件写工具写当前 plan file，其他写目标全部拒绝。
  Date/Author: 2026-06-22 / Codex

- Decision: `/plan` 是 CLI 入口命令，进入计划模式不依赖用户手写 prompt 诱导模型调用 `enter_plan_mode`。
  Rationale: 用户要求在 CLI 中绑定 `/plan` 命令。命令能提供确定入口，模型仍可在复杂任务中主动调用 `enter_plan_mode` 请求进入计划模式。
  Date/Author: 2026-06-22 / Codex

- Decision: explore agent 复用现有 subagent 机制，但在 plan mode 下只能作为只读探索 agent 使用。
  Rationale: OneCode 已有 `agent` 工具和 `SubagentRunner`，重复实现一套 agent 系统会破坏架构边界。完整实现应强化现有 subagent：计划模式父 agent 可以并行发起 explore agent，child runtime 继承 plan context 并受到只读权限硬限制。
  Date/Author: 2026-06-22 / Codex

- Decision: explore agent 并发由目标冲突判断决定，不用单一 `concurrency_safe` 布尔决定。
  Rationale: 用户明确要求访问不同文件可以并发，访问相同文件不能并发。当前 `ToolCallClassification.concurrency_safe` 只能表示工具级可并发，不能表达文件级冲突。因此 executor 需要升级为基于 `ToolTarget` 的冲突图调度。
  Date/Author: 2026-06-22 / Codex

- Decision: 这是重构性质修改，相关旧占位和隐式协议应删除，不保留兼容分支。
  Rationale: 用户明确要求不要为了迁移式安全而保持兼容。保留双轨会让新计划模式和旧占位互相混淆，增加权限和 prompt 事实来源分裂风险。
  Date/Author: 2026-06-22 / Codex


## Outcomes & Retrospective

计划模式的状态、权限、工具、附件和 CLI 入口已落地。本轮补齐 `/plan` 的 REPL 行为：命令只切换模式并返回普通输入框，下一次用户输入会携带 plan-mode attachment 进入 agent；`/plan <描述>` 会把描述作为下一轮用户 prompt 立即运行。验证命令：

- `uv run python -m pytest tests\test_plan_mode.py -q`
- `uv run python -m pytest tests\test_async_cli_streaming.py tests\test_cli_terminal.py -q`
- `uv run python -m pytest tests\test_import_boundaries.py -q`
- `uv run python -m compileall core services infrastructure tools ui`


## Context and Orientation

OneCode 是一个 Python code agent runtime。核心主循环在 `core/loop.py`，它只负责把用户输入追加到消息链、每轮构建模型上下文、调用模型、执行工具、写回工具结果并决定是否继续。任何新能力都不应该在主循环中硬编码工具名或 UI 逻辑。

运行时状态在 `core/runtime_state.py`。当前 `RuntimeState` 有 `usage`、`turn_count`、`session_id` 和 `metadata` 等字段。计划模式实现必须在这里新增正式字段，例如 `permission_mode` 和 `plan`，而不是把计划模式放进 `metadata`。

工具系统在 `services/tools/` 和 `tools/`。`services/tools/types.py` 定义 `ToolDescriptor`、`ToolCallClassification`、`ToolTarget` 和 `ToolExecutionResult`。`services/tools/registry.py` 负责根据当前 state 生成模型可见工具列表。`services/tools/executor.py` 负责工具执行前的 schema validation、工具输入 validation、classification、guard、permission、hook、handler 和结果预算。计划模式应通过这些机制接入，而不是绕过 executor。

权限系统在 `services/permissions/`。`services/permissions/policy.py` 的 `PermissionPolicy.evaluate()` 是执行入口的 deny-first 决策点，`is_tool_visible()` 是工具可见性裁剪入口。计划模式必须同时影响两处：模型看不到计划阶段不能用的工具，即使历史或手写 tool call 仍请求非法工具，也会在执行入口被拒绝。

附件和上下文投影在 `services/attachments/` 和 `services/context/`。`services/attachments/projector.py` 会把内部 `role="attachment"` 的消息投影成 provider-visible user message。当前 `plan_mode` 分支只是占位，这次要替换为完整计划模式说明。

CLI 在 `ui/cli/`。`ui/cli/commands.py` 定义 slash command 注册和分发，`ui/cli/app.py` 负责装配 runtime，`ui/cli/terminal/` 负责内联终端 REPL、权限 modal、临时页面和流式渲染。`/plan` 命令应在 command registry 中注册，TTY 和 batch 行为都必须有清晰路径。

Subagent 在 `services/subagents/` 和 `tools/agent/`。`tools/agent/tool.py` 的 `agent` 工具把请求交给 `SubagentRunner`。计划模式需要允许父 agent 启动只读 explore agent，并让多个 explore agent 在目标不冲突时并发执行。

“权限模式”在本文中指一组影响工具可见性和执行权限的运行模式。普通模式允许常规 agent 工作；计划模式只允许只读探索、用户澄清和写计划文件。“计划文件”是 `.onecode/plans/` 中的 Markdown 文件，agent 在进入计划模式后把发现、取舍和实施步骤写进去。“面试式流程”指 agent 在探索过程中遇到只能由用户决定的问题时，必须用 `ask_user_question` 工具提出结构化问题，而不是用普通文本问答或自行假设。


## Reference Research Notes

本节列出实现者应阅读的 Claude Code 参考文件、从中学习的机制，以及快速定位代码片段的搜索关键词。所有路径均为仓库内参考资料路径。

`docs/references/Tools_full/Tool.ts` 展示 Claude Code 的工具协议如何把 `isReadOnly()`、`isConcurrencySafe()`、`requiresUserInteraction()`、`checkPermissions()`、`validateInput()` 和 `mapToolResultToToolResultBlockParam()` 统一到工具定义中。搜索关键词：`ToolPermissionContext`、`prePlanMode`、`requiresUserInteraction`、`mapToolResultToToolResultBlockParam`。

`docs/references/Tools_full/tools.ts` 展示 EnterPlanMode、ExitPlanMode 和 AskUserQuestion 都是普通工具池成员，并且工具池会基于权限规则过滤。搜索关键词：`EnterPlanModeTool`、`ExitPlanModeV2Tool`、`AskUserQuestionTool`、`filterToolsByDenyRules`、`assembleToolPool`。

`docs/references/Tools_full/EnterPlanModeTool/EnterPlanModeTool.ts` 展示进入计划模式的状态转换：拒绝 agent 子上下文调用、调用 `handlePlanModeTransition`、保存进入前模式、设置 mode 为 `plan`、在 tool result 中告诉模型进入只读计划流程。搜索关键词：`handlePlanModeTransition`、`prepareContextForPlanMode`、`setMode`、`mapToolResultToToolResultBlockParam`。

`docs/references/Tools_full/EnterPlanModeTool/prompt.ts` 展示模型何时应该主动请求进入计划模式。OneCode 不必照搬文案，但应学习它区分复杂任务和简单任务的准则。搜索关键词：`When to Use This Tool`、`When NOT to Use This Tool`、`Important Notes`。

`docs/references/Tools_full/ExitPlanModeTool/ExitPlanModeV2Tool.ts` 展示退出计划模式的核心机制：非 plan mode 调用要 validate 失败，真正退出前读取计划文件并请求用户确认，批准后恢复 `prePlanMode`，设置 `hasExitedPlanMode` 和 `needsPlanModeExitAttachment`，tool result 把批准后的计划返回模型。搜索关键词：`validateInput`、`checkPermissions`、`getPlanFilePath`、`getPlan`、`prePlanMode`、`setHasExitedPlanMode`、`setNeedsPlanModeExitAttachment`、`Approved Plan`。

`docs/references/Tools_full/ExitPlanModeTool/prompt.ts` 展示 ExitPlanMode 的使用边界：只能在写完计划后请求审批，不能用于纯研究任务，也不能让 `AskUserQuestion` 代替审批。搜索关键词：`Before Using This Tool`、`Do NOT use AskUserQuestion`、`Examples`。

`docs/references/Tools_full/AskUserQuestionTool/AskUserQuestionTool.tsx` 展示结构化用户提问工具：输入包括问题、短标题、选项、可选 preview，工具需要用户交互，结果把用户回答写回模型。OneCode 初版不需要 HTML preview，但要保留多问题、选项、自由输入和审批禁用边界。搜索关键词：`questionSchema`、`requiresUserInteraction`、`checkPermissions`、`answers`、`User has answered your questions`。

`docs/references/Tools_full/AskUserQuestionTool/prompt.ts` 展示计划模式中提问工具的限制：用它澄清需求和选择方案，不用它请求计划是否通过。搜索关键词：`Plan mode note`、`Do NOT use this tool to ask`、`If you need plan approval`。

`docs/references/ui/bootstrap/state.ts` 展示 Claude Code 把计划模式相关状态放在 bootstrap state 中，包括 `hasExitedPlanMode`、`needsPlanModeExitAttachment`、`planSlugCache` 和 `parentSessionId`。OneCode 应把这些概念放入 `RuntimeState` 的正式字段或 `services/plans` 状态对象中。搜索关键词：`hasExitedPlanMode`、`needsPlanModeExitAttachment`、`handlePlanModeTransition`、`planSlugCache`、`parentSessionId`。

`docs/references/ui/utils/plans.ts` 展示计划文件目录、slug、resume 恢复、fork 复制和 transcript fallback。OneCode 应学习其“计划文件有稳定 slug、fork 不共享同一文件、resume 能恢复计划内容”的机制，但路径改为 `.onecode/plans/`。搜索关键词：`getPlansDirectory`、`getPlanSlug`、`getPlanFilePath`、`copyPlanForResume`、`copyPlanForFork`、`recoverPlanFromMessages`、`plan_file_reference`。

`docs/references/ui/utils/messages.ts` 展示 plan-mode attachment 文案和面试式流程。OneCode 应优先采用 `getPlanModeInterviewInstructions()` 的思路：探索、更新计划、向用户提问，不把五阶段 agent-heavy 流程作为唯一工作流。搜索关键词：`getPlanModeInstructions`、`getPlanModeV2Instructions`、`getPlanModeInterviewInstructions`、`plan_mode_reentry`、`plan_mode_exit`、`Ending Your Turn`。

`docs/references/Tools_full/services/tools/toolOrchestration.ts` 和 `docs/references/Tools_full/services/tools/StreamingToolExecutor.ts` 展示工具并发执行与按序产出结果的参考。OneCode 当前 executor 已有并发批次，但本计划要升级为目标冲突感知调度。搜索关键词：`partitionToolCalls`、`isConcurrencySafe`、`StreamingToolExecutor`、`canExecuteTool`、`getCompletedResults`。


## Plan of Work

第一阶段是把计划模式状态提升为正式核心状态。编辑 `core/runtime_state.py`，新增 `PermissionMode` 和 `PlanState`。`PermissionMode` 至少包含 `DEFAULT` 和 `PLAN`；如果未来已有其他模式，再在同一个枚举里扩展。`PlanState` 保存进入前模式、是否已退出计划模式、是否需要注入 plan attachment、是否需要注入 plan exit attachment、计划 slug、父 session id。`RuntimeState.start_new_session()` 要重置 plan state，但如果调用方明确是“批准计划并开始 fresh context”，应通过专用 helper 把批准后的 plan path 或 plan content 作为 plan exit attachment 保留下来。不要把计划模式事实写进 `metadata`。

第二阶段新增 `services/plans/`。`services/plans/store.py` 负责 `.onecode/plans/` 的路径和文件管理。`PlanStore` 接收 workspace，确保 plans 目录存在，提供 `get_or_create_plan_path(state, agent_id=None)`、`read_plan(state, agent_id=None)`、`copy_for_fork(source_state, target_state)`、`recover_for_resume(state, messages)` 等函数。函数只处理计划文件，不执行模型或工具。恢复逻辑只支持新的 `.onecode/plans/` 和新 transcript attachment，不为旧占位格式做兼容。

第三阶段新增计划模式工具。创建 `tools/enter_plan_mode/`、`tools/exit_plan_mode/` 和 `tools/ask_user_question/`，每个目录遵循现有工具模式，提供 `tool.py`、`prompt.py` 和 `__init__.py`。`enter_plan_mode` 的 handler 只改变 runtime state 和准备计划文件；`exit_plan_mode` 的 validate 阶段必须在非 plan mode 时返回结构化错误，不应弹出审批；`ask_user_question` 通过 permission prompter 或新的交互 prompter 收集用户答案。工具结果要用普通 `ToolExecutionResult` 表达，并把模型需要继续推理的内容放入 `content`。

第四阶段改造权限系统。编辑 `services/permissions/policy.py`，让 `evaluate()` 在普通 deny-first 检查后应用 plan mode 专用规则。计划模式不是 prompt 建议，而是代码边界。`write_file` 和 `edit_file` 只有在唯一写目标等于当前计划文件路径时允许；`bash` 只有 classifier 判定只读时允许；`agent` 只有输入表示 explore agent 且 child 会被强制只读时允许；`exit_plan_mode`、`ask_user_question`、`read_file`、`grep`、`glob` 允许；其他工具拒绝。`is_tool_visible()` 也要按同样规则裁剪整工具，对于输入相关的工具如 `write_file` 无法仅凭 descriptor 判断具体路径，所以可以让它在 plan mode 可见，但 prompt 必须说明只能写计划文件，执行入口必须重复校验路径。

第五阶段替换 attachment 投影。编辑 `services/attachments/projector.py`，删除当前简单的 `plan_mode` 文本包装，改为调用 `services/plans/attachments.py` 或 `services/plans/prompts.py` 生成完整 provider-visible user message。`plan_mode` 注入面试式计划流程；`plan_mode_reentry` 要求先读已有计划并判断继续或覆盖；`plan_mode_exit` 告诉模型计划已批准，可以实施，并附计划文件路径。相关测试从“只要不暴露 raw attachment role”升级为断言具体文案包含计划文件、只读限制、`ask_user_question` 和 `exit_plan_mode`。

第六阶段把计划 attachment 接入上下文准备。当前附件收集由 CLI 在用户输入前收集普通 attachments，再由 `AttachmentContextPreparer` 投影。计划模式 attachment 是 runtime-generated，不应要求用户手动输入。可以在 `AttachmentCollector` 增加一个 shared source，或在专门的 plan context preparer 中根据 `state.plan.needs_plan_mode_attachment` 和 `state.plan.needs_plan_mode_exit_attachment` 产生 durable attachment。实现者应选择与现有 attachment 洋葱链最一致的方式，但必须保证 attachment 进入 `MessageStore`，可被 transcript 恢复和 compaction 保留。

第七阶段绑定 CLI `/plan` 命令。编辑 `ui/cli/commands.py` 注册 `/plan`。当不在 plan mode 时，`/plan` 应直接触发进入计划模式的用户确认流程，确认后更新 runtime state 并显示计划文件路径；如果用户在命令后写了描述，例如 `/plan add auth flow`，该描述应作为下一轮用户 prompt 或 queued command 进入 agent，让模型在 plan mode 下开始探索。已在 plan mode 时，`/plan` 显示当前计划内容和路径。`/plan open` 在不需要 GUI 的环境中至少返回计划文件路径；如果实现者选择打开外部编辑器，必须走 CLI 可控的交互路径，并记录新的 Decision。

第八阶段让 `ui/cli/app.py` 注册新工具并装配 plan store。`build_runtime()` 应创建 `PlanStore(workspace)`，把它注入计划工具、permission policy、prompt/attachment helper 和 `CliRuntime`。`CliRuntime.with_session()`、`with_model_config()` 和 resume 路径要重建或保留 plan store 引用。`ui/cli/types.py` 应新增 `plan_store` 字段。不要把计划路径藏进工具局部闭包导致 `/plan` 命令和工具读到不同计划。

第九阶段改造 subagent 和 explore agent。`tools/agent/tool.py` 的 input schema 应支持 `subagent_type="explore"` 和可选 `focus_paths`。在 plan mode 下，只有 explore agent 可用；child runtime 必须强制只读，继承当前 plan mode 的只读约束，但不能写父计划文件，除非明确设计 agent-specific plan 文件。`services/subagents/runner.py` 应把 `focus_paths` 转换为 `ToolTarget`，供父 executor 做冲突调度。child 的可见工具不应包含 `agent`，避免递归。

第十阶段重构 executor 并发调度。当前 `services/tools/executor.py` 使用 `ToolCallClassification.concurrency_safe` 做连续批次并发。完整实现要改为基于 `ToolTarget` 的冲突判断。新增内部 helper，例如 `targets_conflict(left, right) -> bool` 和 `build_conflict_batches(ready_calls) -> list[list[_ReadyToolCall]]`。规则是：同一文件写写、读写、写读冲突；同一文件的多个只读可并发；目录与其子路径冲突；未知目标或空目标按保守独占；非只读工具默认独占，除非其 target 明确属于 runtime-managed state 且已有锁保护。explore agent 的 `focus_paths` 不重叠时可以并发，重叠时串行。实现时保留 executor 的公开协议，不改 `AgentLoop`。

第十一阶段删除旧占位和更新测试。删除或替换所有仅为旧 plan_mode 占位服务的测试断言。不要保留 `metadata["plan_file_path"]`、`metadata["permission_mode"]` 或旧 attachment 文案。新增测试应证明新状态、新路径、新工具、新 CLI 命令、新权限和新并发规则都实际工作。


## Concrete Steps

所有命令都从仓库根目录 `D:\study\OneCode` 运行。

实现前先确认当前工作树状态：

    git status --short

查找现有计划占位和权限相关代码：

    rg -n "plan_mode|permission_mode|read_only_agent|concurrency_safe|ToolTarget|/plan" core services tools ui tests docs

实现过程中按阶段运行聚焦测试。新增 state 和 plan store 后运行：

    uv run python -m pytest tests/test_runtime_state.py tests/test_jsonl_session_persistence.py -q

新增工具和权限后运行：

    uv run python -m pytest tests/test_permission_policy.py tests/test_tool_registry_and_executor.py tests/test_project_permission_settings.py -q

新增 attachment 和 CLI 命令后运行：

    uv run python -m pytest tests/test_attachment_projector.py tests/test_cli_commands.py tests/test_async_cli_streaming.py -q

新增 subagent 和并发调度后运行：

    uv run python -m pytest tests/test_agent_tool.py tests/test_subagent_runner.py tests/test_tool_registry_and_executor.py -q

最后运行全量测试和 import boundary：

    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests/test_import_boundaries.py -q

CLI 手工验证时运行：

    uv run python -m ui.cli.app

在交互界面输入：

    /plan add a small test-only feature plan

预期能看到计划模式确认、计划文件路径在 `.onecode/plans/` 下、后续模型只能执行只读探索和写计划文件。再次输入：

    /plan

预期显示当前计划文件内容或空计划提示。输入：

    /plan open

预期显示可打开的计划文件路径，或在实现外部编辑器支持时打开该文件。


## Validation and Acceptance

行为验收一：`/plan` 能进入计划模式。启动 CLI 后输入 `/plan <描述>`，用户确认后，`RuntimeState.permission_mode` 为 `PLAN`，`.onecode/plans/<slug>.md` 存在或路径已分配，模型下一轮上下文包含计划模式说明。测试应通过 fake runtime 或 command dispatch 断言 `/plan` 更新 state 并返回计划路径。

行为验收二：计划模式只允许写计划文件。构造 fake model，让它在 plan mode 下调用 `write_file` 写当前 plan path，应成功；调用 `write_file` 写 `src/example.py` 或调用 `edit_file` 改普通文件，应返回 permission deny，并且目标文件不变。测试应断言 deny 来源是 plan mode policy，而不是 handler 偶然失败。

行为验收三：计划模式下非只读命令被拒绝。fake model 调用 `bash` 运行只读命令如 `git status --short` 应按现有 bash 分类流程执行；运行 `touch x.txt`、`git add .` 或 redirect 写文件应被 permission policy 拒绝。测试应断言文件系统没有新增文件。

行为验收四：面试式提问工具能收集答案。fake prompter 返回用户选择后，`ask_user_question` 的 tool result 应包含用户答案，模型下一轮能看到该答案。若用户拒绝回答，工具返回结构化拒绝结果，计划模式保持 active。

行为验收五：`exit_plan_mode` 正式审批。非 plan mode 调用该工具应 validate 失败，不弹用户审批。plan mode 下调用该工具应读取 `.onecode/plans/<slug>.md`，向用户展示计划内容；批准后恢复进入前 mode，设置 plan exit attachment，tool result 包含 Approved Plan；拒绝后仍处于 plan mode，tool result 包含用户反馈。

行为验收六：plan attachments 有完整语义。`plan_mode` 投影后必须包含计划文件路径、只读限制、唯一可编辑文件、探索-更新-提问循环、`ask_user_question` 和 `exit_plan_mode` 的收束要求。`plan_mode_exit` 投影后必须说明已经退出计划模式，可以实施。

行为验收七：explore agent 只读且冲突感知并发。两个 explore agent 的 `focus_paths` 指向不同文件时，executor 可以并发启动；两个 explore agent 指向同一文件或父子目录时，executor 串行。Explore child 尝试写普通文件时必须被拒绝。测试可使用 fake handler 记录开始/结束顺序和 barrier，证明不冲突目标重叠执行、冲突目标不重叠执行。

行为验收八：工具可见性正确。plan mode 下模型可见工具中不应出现实施阶段专用工具；普通模式下计划工具仍可见或可 deferred，但普通工具不受 plan mode 裁剪。测试应比较 `ToolRegistry.tool_schemas(state)` 在 default 和 plan 两种 mode 下的差异。

行为验收九：旧占位被删除。搜索 `rg -n "reserved|\\[plan mode attachment\\]|metadata\\[\"permission_mode\"\\]|metadata\\[\"plan_file_path\"\\]" services tools ui tests` 不应命中新实现中的旧协议。若命中，应只有文档说明或测试 fixture 中明确的负例。


## Idempotence and Recovery

创建 `.onecode/plans/` 是幂等的，目录已存在时不报错。`PlanStore.get_or_create_plan_path()` 对同一 session 应返回同一 slug 和路径，除非 `/clear` 或 fork 明确要求新计划。重复运行 `/plan` 时，如果当前已经在 plan mode，不应创建新计划文件；它应显示当前计划或提示继续编辑。退出计划模式被拒绝时，不应清空计划文件或恢复 pre-plan mode。

实现过程中如果某个阶段测试失败，先保留新结构化 state 和 plan store，不要退回 metadata 兼容路径。可以用 `git diff` 查看本轮改动，并针对失败测试修复。不要使用 `git reset --hard` 或删除用户未要求删除的文件。

并发调度重构风险较高。若冲突判断导致现有只读工具并发测试失败，应修正 target conflict helper，而不是恢复旧的 `concurrency_safe` 单布尔模型。旧字段可以保留为“是否参与并发候选”的输入，但最终并发必须由 targets 冲突决定。


## Interfaces and Dependencies

在 `core/runtime_state.py` 中定义：

    class PermissionMode(StrEnum):
        DEFAULT = "default"
        PLAN = "plan"

    @dataclass
    class PlanState:
        pre_plan_mode: PermissionMode | None = None
        has_exited_plan_mode: bool = False
        needs_plan_mode_attachment: bool = False
        needs_plan_mode_exit_attachment: bool = False
        plan_slug: str | None = None
        parent_session_id: str | None = None

    @dataclass
    class RuntimeState:
        permission_mode: PermissionMode = PermissionMode.DEFAULT
        plan: PlanState = field(default_factory=PlanState)

在 `services/plans/store.py` 中定义 `PlanStore`。它不依赖 `core.loop`，只依赖 `RuntimeState`、`Path` 和标准库文件操作。它应负责 `.onecode/plans/` 路径、slug、读写计划内容、fork 复制和 resume 恢复。

在 `services/plans/transitions.py` 或 `services/plans/state.py` 中定义计划模式转换 helper，例如 `enter_plan_mode(state, plan_store)` 和 `exit_plan_mode(state, approved=True)`。这些 helper 只改 state，不执行工具，不调用模型。

在 `tools/enter_plan_mode/tool.py`、`tools/exit_plan_mode/tool.py` 和 `tools/ask_user_question/tool.py` 中定义工具 descriptor。它们应使用 `ToolDescriptor`，通过现有 `RegistryToolExecutor` 执行。

在 `services/permissions/policy.py` 中扩展 `PermissionPolicy.evaluate()` 和 `is_tool_visible()`。plan mode deny 必须在执行入口生效，不能只靠 prompt。

在 `services/tools/executor.py` 中引入目标冲突调度 helper。公开 `ToolExecutor.execute()` 协议保持不变。

在 `tools/agent/tool.py` 和 `services/subagents/runner.py` 中扩展 explore agent 的 `focus_paths` 和只读 child runtime 设置。不要让 child runtime 再暴露 `agent` 工具。

在 `ui/cli/commands.py` 中注册 `/plan`。在 `ui/cli/app.py` 和 `ui/cli/types.py` 中注入并保存 `PlanStore`。


## Artifacts and Notes

关键参考搜索命令：

    rg -n "getPlanModeInterviewInstructions|plan_mode_reentry|plan_mode_exit|Ending Your Turn" docs/references/ui/utils/messages.ts
    rg -n "getPlanSlug|getPlansDirectory|getPlanFilePath|copyPlanForResume|copyPlanForFork|recoverPlanFromMessages" docs/references/ui/utils/plans.ts
    rg -n "handlePlanModeTransition|hasExitedPlanMode|needsPlanModeExitAttachment|planSlugCache|parentSessionId" docs/references/ui/bootstrap/state.ts
    rg -n "validateInput|checkPermissions|prePlanMode|Approved Plan|setHasExitedPlanMode" docs/references/Tools_full/ExitPlanModeTool/ExitPlanModeV2Tool.ts
    rg -n "requiresUserInteraction|User has answered your questions|Plan mode note" docs/references/Tools_full/AskUserQuestionTool
    rg -n "partitionToolCalls|StreamingToolExecutor|canExecuteTool|isConcurrencySafe" docs/references/Tools_full/services/tools

当前仓库中需要替换的旧 plan-mode 占位可用：

    rg -n "plan_mode" services tests ui tools core

实现结束后应在本文件底部追加一条变更记录，说明完成的实现范围、测试命令和任何偏离本计划的原因。


2026-06-22 / Codex: 初始版本。根据用户要求创建完整中文 ExecPlan，明确计划模式不是 MVP，而是一等运行时模式；计划文件放入 `.onecode/plans/`；不新增计划写工具；CLI 绑定 `/plan`；旧 plan-mode 占位和 metadata 隐式协议要删除；explore agent 复用 subagent，并通过目标冲突感知调度实现不同文件可并发、相同文件不可并发。
