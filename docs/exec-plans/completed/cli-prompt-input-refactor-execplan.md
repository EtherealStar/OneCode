# 重构 CLI PromptInput 为单状态机输入系统

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵循仓库根目录的 `PLANS.md`。实现者必须把本文当作唯一上下文来执行；如果实现过程中发现事实变化，先更新本文，再继续改代码。

## Purpose / Big Picture

OneCode 当前 CLI 能作为增强 REPL 使用，但输入交互仍以“一次读取一行字符串”为核心。主 prompt、权限确认、provider 连接、MCP trust prompt、page mode 和 selector 各自拥有输入逻辑或临时循环。已有轻量计划 `docs/exec-plans/active/cli-prompt-input-lightweight-plan.md` 试图通过 prompt-toolkit、editable key-reader、内置 input 三层后端修复可靠性问题；这个方向能缓解 Backspace 失败，却会在补全、历史、粘贴、多行、队列通知和浮层交互上制造重复行为。

本计划的目标是把 CLI 输入重构为“单输入状态机 + 薄终端驱动”。用户完成本计划后应能在主 prompt 中获得一致的行编辑、历史、slash command 建议、`/resume` 候选和 `@file` 候选；在 Windows/Codex desktop 这类 `stdin` 可交互但 `stdout` 不被普通 TTY 检测认可的环境中，Backspace、Delete 和光标移动仍可预测；权限、connect 和 trust prompt 后续可以复用同一输入系统，而不是维护独立后端。

可观察结果是：运行 `uv run python -m ui.cli.app` 后，用户在同一个 prompt 输入中可以键入 `abc`、按 Backspace、提交得到 `ab`；键入 `/` 后能看到命令建议；键入 `@` 后能看到 workspace 内文件候选；运行测试时，纯状态机测试和 CLI 输入集成测试稳定通过。

## Progress

- [x] (2026-06-10) 阅读 `PLANS.md`，确认 ExecPlan 必须自包含、可执行、持续维护，并包含 Progress、Surprises & Discoveries、Decision Log、Outcomes & Retrospective。
- [x] (2026-06-10) 阅读 `docs/design-docs/cli-architecture.md`、`docs/exec-plans/active/cli-prompt-input-lightweight-plan.md` 和 `docs/references/ui/components/PromptInput`，确认现有轻量计划与本次重构目标冲突。
- [x] (2026-06-10) 决定新建完整 ExecPlan，而不是覆盖轻量计划；轻量计划保留为历史背景，新计划作为后续实现事实来源。
- [x] (2026-06-10) 建立 `ui/cli/prompt_input/` 包，完成纯状态、事件、光标编辑 reducer、suggestion provider 和聚焦测试；旧 `OneCodeCompleter` 已改为消费同一 suggestion provider。
- [x] (2026-06-10) 用新的 `prompt_input.session.read_prompt()` 替换主 prompt 的普通输入路径；`main_loop_async()` 现在消费 `PromptSubmission`，slash command 分发和 agent loop 边界保持不变。
- [x] (2026-06-11) 将 `/resume` selector、`/connect`、权限确认和 MCP trust prompt 分阶段迁移到同一输入系统或同一 modal 输入模型。`/resume` 和 `/connect` 使用 selector modal；connect 文本/API key、权限确认和 MCP trust prompt 复用 `ui.cli.input` 的单行输入兼容层。
- [x] (2026-06-10) 更新 `docs/design-docs/cli-architecture.md`，使架构文档描述新 PromptInput 子系统、`input.py` 兼容层和当前迁移边界。
- [x] (2026-06-11) 完成自动验收测试并记录结果到 Outcomes & Retrospective；当前环境未执行人工 `uv run python -m ui.cli.app` 交互验收。

## Surprises & Discoveries

- Observation: `docs/exec-plans/active/cli-prompt-input-lightweight-plan.md` 明确提出三层后端顺序：prompt-toolkit、editable key-reader、内置 input。该计划适合小修 Backspace，但与本次“彻底重构输入与交互部分，不保留三层后端架构”的目标相反。
  Evidence: 轻量计划的“后端顺序”章节把三层实现列为设计方案。

- Observation: `docs/references/ui/components/PromptInput/PromptInput.tsx` 的价值不在 React/Ink 组件本身，而在输入状态模型。它显式维护 `cursorOffset`、`pastedContents`、`suggestionsState`、history search、queued commands、mode indicator 和 keybinding context。
  Evidence: 参考文件中 `cursorOffset`、`pastedContents`、`suggestionsState`、`queuedCommands`、`handleUndo`、`handleNewline`、`handleExternalEditor` 等状态和操作都围绕同一个 PromptInput 组件协调。

- Observation: 当前 `ui/cli/input.py` 的 `OneCodeCompleter` 已经拥有命令、`/resume` 和 `@file` 补全知识，但这些知识绑定在 prompt-toolkit `Completer` 上。若不先抽出 provider-neutral suggestion provider，任何 fallback 或自定义 driver 都会重复实现候选生成。
  Evidence: `OneCodeCompleter.get_completions()` 直接调用 `_command_completions()`、`_resume_candidates()` 和 `_file_candidates()`。

- Observation: 第一阶段可以保持主 CLI 的 prompt-toolkit 入口不变，同时把补全事实来源迁移到 `ui/cli/prompt_input/suggestions.py`。
  Evidence: `ui/cli/input.py` 的 `OneCodeCompleter.get_completions()` 现在只把 `SuggestionItem` 转成 prompt-toolkit `Completion`；`uv run python -m pytest tests\test_cli_prompt_input_state.py tests\test_cli_prompt_input_suggestions.py tests\test_cli_completion.py -q` 通过，输出 `16 passed`。

- Observation: 主 prompt 可以先通过 `prompt_input.session.read_prompt()` 返回 `PromptSubmission`，而不要求一次性重写 prompt-toolkit 的完整 terminal UI。
  Evidence: `ui/cli/app.py` 现在根据 `submission.kind` 调用 `dispatch_command()` 或 agent loop；`uv run python -m pytest tests\test_async_cli_streaming.py tests\test_cli_commands.py tests\test_cli_prompt_input_state.py tests\test_cli_prompt_input_suggestions.py tests\test_cli_prompt_input_sync.py tests\test_cli_completion.py tests\test_import_boundaries.py -q` 通过，输出 `43 passed`。

- Observation: MCP trust prompt 发生在 `build_runtime()` 的同步装配阶段，不能直接等待异步主 prompt session。
  Evidence: `ui/cli/app.py` 的 `_prompt_for_project_mcp_trust()` 现在调用 `read_line_sync()`，并由 `tests/test_cli_mcp_trust_prompt.py` 验证 trust/EOF 两条路径。

## Decision Log

- Decision: 本次重构不采用“三层后端”作为目标架构，而采用“单输入状态机 + 薄终端驱动”。
  Rationale: 三层后端会让编辑、补全、历史和粘贴行为在多个实现中分叉。单状态机让行为只有一个事实来源，终端差异只影响按键读取和画面输出。
  Date/Author: 2026-06-10 / Codex

- Decision: 不迁移 React/Ink，也不直接复刻 `PromptInput.tsx` 的 UI 组件树。
  Rationale: OneCode 是 Python runtime，当前 CLI 使用 prompt-toolkit 和 Rich。参考目录的核心启发是状态建模和事件仲裁，不是前端框架或具体视觉样式。
  Date/Author: 2026-06-10 / Codex

- Decision: 第一阶段不加入 `!bash` 快捷模式、Vim 模式、语音输入、鼠标定位或全屏 overlay。
  Rationale: 这些能力会扩大交互面，并牵涉 BashTool 权限语义或额外终端能力。先交付可靠输入状态机、建议、历史和主 prompt 接入。
  Date/Author: 2026-06-10 / Codex

- Decision: Slash command 的业务分发继续由 `ui/cli/commands.py` 负责。
  Rationale: 输入系统只负责编辑、建议和提交结构化输入；命令执行、runtime 替换、page 展示和 agent loop 调用仍属于 CLI application 层。
  Date/Author: 2026-06-10 / Codex

- Decision: 长粘贴折叠作为后续里程碑，存储方式必须对齐 attachment 或 transcript 体系，不能引入不可追踪的隐藏 paste store。
  Rationale: OneCode 的上下文治理要求可恢复、可观察。隐藏粘贴内容若不进入 durable artifact，会破坏 transcript 和调试能力。
  Date/Author: 2026-06-10 / Codex

## Outcomes & Retrospective

第一阶段已完成纯状态模型、统一 suggestion provider 和主 prompt `PromptSubmission` 接入。新增 `ui/cli/prompt_input/state.py`、`events.py`、`editor.py`、`reducer.py`、`suggestions.py` 和 `session.py`，并新增 `tests/test_cli_prompt_input_state.py`、`tests/test_cli_prompt_input_suggestions.py`、`tests/test_cli_prompt_input_sync.py`。当前覆盖范围是：编辑状态、光标删除/插入、多字节和多行文本、历史上下翻、建议接受、命令建议、`/resume` session 建议、workspace 内当前层级 `@file` 建议、同步 fallback Backspace/光标删除和 password 不回显。

`ui/cli/input.py` 仍保留 prompt-toolkit 读行、非交互 `input()` fallback 和 password 输入作为兼容层，但 prompt-toolkit completer 已不再拥有自己的候选生成逻辑，而是消费 `suggestions_for()`；Windows editable fallback 也已通过 reducer 执行编辑。`ui/cli/app.py` 的 `main_loop_async()` 已直接消费 `PromptSubmission`。`/resume` selector、`/connect` provider 选择使用 `pages.select_item()` modal；connect 的 base URL/API key/model、权限确认和 MCP trust prompt 复用 `ui.cli.input` 的单行输入兼容层，其中 API key 仍以 password mode 读取。

验证已通过：

    uv run python -m compileall ui\cli\app.py ui\cli\input.py ui\cli\prompt_input
    uv run python -m pytest tests\test_cli_prompt_input_state.py tests\test_cli_prompt_input_suggestions.py tests\test_cli_prompt_input_sync.py tests\test_cli_completion.py -q
    uv run python -m pytest tests\test_async_cli_streaming.py tests\test_cli_commands.py tests\test_cli_prompt_input_state.py tests\test_cli_prompt_input_suggestions.py tests\test_cli_prompt_input_sync.py tests\test_cli_completion.py tests\test_import_boundaries.py -q

观察到的结果分别是新增输入模块编译通过、`19 passed` 和 `43 passed`。尚未进行人工 `uv run python -m ui.cli.app` 交互验收。

2026-06-11 继续验证通过：

    uv run python -m pytest tests\test_cli_connect.py tests\test_cli_mcp_trust_prompt.py tests\test_cli_prompt_input_sync.py tests\test_import_boundaries.py -q
    uv run python -m pytest tests\test_cli_prompt_input_state.py tests\test_cli_prompt_input_suggestions.py tests\test_cli_prompt_input_sync.py tests\test_cli_completion.py tests\test_cli_pages.py tests\test_cli_resume.py tests\test_cli_connect.py tests\test_cli_mcp_trust_prompt.py tests\test_cli_commands.py tests\test_async_cli_streaming.py tests\test_import_boundaries.py -q
    uv run python -m compileall ui\cli\app.py ui\cli\connect.py ui\cli\input.py ui\cli\prompt_input
    uv run python -m pytest tests -q

观察到的结果分别是 `11 passed`、`59 passed`、编译通过和 `364 passed`。当前 Codex 非交互执行环境未进行人工 `uv run python -m ui.cli.app` 键盘验收；该风险限制在终端驱动真实按键表现，状态机、fallback 和命令集成已有自动测试覆盖。

## Context and Orientation

OneCode 的 CLI 位于 `ui/cli/`。`ui/cli/app.py` 负责构建 runtime 和运行主循环；`main_loop_async()` 当前调用 `prompt_async(lambda: runtime)` 读取一行文本，若文本以 `/` 开头则交给 `ui/cli/commands.py` 的 `dispatch_command()`，否则调用 `runtime.attachment_collector.collect_for_user_turn()` 收集附件，再把文本交给 `AgentLoop.stream()`。这条边界必须保持：CLI 输入不实现 agent 主循环，不执行工具，不判断 provider 协议。

`ui/cli/input.py` 当前混合了三个职责。第一，它用 prompt-toolkit `PromptSession.prompt_async()` 在正常 TTY 中读取输入。第二，它在 prompt-toolkit 失败或 Windows 特定环境中回退到 `_read_line_with_key_reader()`。第三，它在非交互环境中调用内置 `input()` 或 `getpass()`。同一文件还包含 `OneCodeCompleter`，负责 slash command、`/resume` 参数和 `@file` 候选。

`ui/cli/pages.py` 当前提供 `show_page()` 和 `select_item()`，分别拥有自己的 prompt-toolkit `Application` 和 key bindings。它们是临时 page mode，不进入 runtime。后续应把 selector 和 page 的按键所有权纳入同一交互模型，但第一阶段不要强行重写所有 page。

`ui/cli/connect.py` 和 `ui/cli/permissions.py` 负责 provider 连接向导和工具权限确认。它们的输入行为应逐步复用新的输入 session 或 modal input API。实现时必须保证 API key 输入不回显。

`services/attachments/` 是附件系统。用户输入中的 `@file` 由 `AttachmentCollector.collect_for_user_turn()` 在调用 agent loop 前收集，并在模型调用前由 `AttachmentContextPreparer` 投影。输入系统可以提供 `@file` 建议，但不应自己读取文件或制造 provider-visible attachment。

本计划使用以下术语：

“输入状态机”指一个纯 Python 对象模型，它保存输入文本、光标位置、历史状态、建议列表、选择索引和当前 modal 状态。它接收按键事件，返回新状态和待执行副作用。纯状态机不直接读键盘、不写终端、不调用 agent。

“终端驱动”指负责从终端读取按键、把状态渲染结果写回屏幕的薄适配层。驱动可以因 Windows、普通 TTY、测试环境而不同，但它们不得各自实现编辑、补全或历史规则。

“建议 provider”指根据当前输入文本和光标位置生成候选项的函数。候选项用于 `/status` 这类命令补全、`/resume <session>` 参数补全和 `@path` 文件补全。

“提交”指用户按 Enter 后产生的 `PromptSubmission`。提交可能是普通 agent prompt，也可能是 slash command 文本；它不是 agent response。

## Plan of Work

第一步是建立新的包 `ui/cli/prompt_input/`，只实现纯状态和测试。创建 `state.py`，定义 `PromptInputState`、`BufferState`、`SuggestionState`、`SuggestionItem`、`PromptSubmission` 等 dataclass。`BufferState` 至少包含 `text: str` 和 `cursor: int`。`SuggestionItem` 至少包含 `id`、`kind`、`display`、`replacement`、`description`。`PromptSubmission` 至少包含 `text` 和 `kind`，其中 `kind` 可以是 `"prompt"` 或 `"command"`。这些类型不导入 `core/` 或具体工具。

第二步创建 `events.py` 和 `reducer.py`。`events.py` 定义小型事件类型，例如 `TextInserted`、`KeyPressed`、`SuggestionOpened`、`SuggestionAccepted`、`HistoryPreviousRequested`。如果使用字符串枚举，应把每个键名写清楚，例如 `"backspace"`、`"delete"`、`"left"`、`"right"`、`"home"`、`"end"`、`"ctrl_u"`、`"ctrl_w"`、`"enter"`、`"escape"`、`"tab"`。`reducer.py` 提供 `apply_event(state, event) -> ReducerResult`。`ReducerResult` 包含新状态、可选提交和需要驱动执行的副作用。第一阶段副作用可以只包括 `refresh_suggestions`。

第三步创建 `editor.py`，实现光标感知编辑函数：插入文本、删除光标前字符、删除光标处字符、左右移动、Home/End、清空输入、删除前一个词、插入换行。所有函数都必须是纯函数，接收 `BufferState` 返回新的 `BufferState`。测试应覆盖光标位于中间、行首、行尾、空输入、多字节普通字符和多行文本。即使渲染层暂不支持完整多行，状态层也应允许 `\n`，这样后续不用改核心模型。

第四步创建 `suggestions.py`，从现有 `ui/cli/input.py` 中抽出候选生成逻辑。提供 `suggestions_for(runtime, text, cursor) -> tuple[SuggestionItem, ...]`，并把命令、`/resume`、`@file` 都建模成同一结构。prompt-toolkit 兼容层后续也应消费这个 provider，而不是继续直接调用 `_command_completions()`。文件候选必须保持 workspace 边界：只遍历 workspace 内当前路径层级，不做无界递归。

第五步创建一个可测试的 `memory_driver.py` 或测试 helper，不接触真实终端。它接收事件序列，运行 reducer，返回最终提交。新增 `tests/test_cli_prompt_input_state.py`、`tests/test_cli_prompt_input_suggestions.py` 和 `tests/test_cli_prompt_input_submission.py`。这一步完成后，即使没有接入主 CLI，也能证明输入行为稳定。

第六步实现真实输入 session。创建 `session.py`，提供 `async def read_prompt(runtime_provider: Callable[[], CliRuntime]) -> PromptSubmission`。它使用新的状态机和终端驱动读取主 prompt。初始可以保留 `ui/cli/input.py` 的公共函数名 `prompt_async()`，但内部委托到 `prompt_input.session.read_prompt()` 并返回提交文本，或在后续改 `main_loop_async()` 直接消费 `PromptSubmission`。为降低风险，第一次接入时只替换主 prompt，权限、connect 和 page 暂不迁移。

第七步改 `ui/cli/app.py` 的 `main_loop_async()`。读取 `PromptSubmission` 后，如果提交文本以 `/` 开头，仍调用 `dispatch_command(runtime, text)`；否则按现有顺序收集附件并调用 `runtime.loop.stream(text, attachments)`。这一步不改变 agent loop、context engine、tool executor 或 permission policy。

第八步迁移其他输入入口。`ui/cli/connect.py` 的 provider/model/base URL/API key 输入应改为复用新的单行输入 API，其中 API key 使用 password mode。`ui/cli/permissions.py` 的权限确认 prompt 也应复用同一输入 API。MCP trust prompt 如果仍有直接 `input()`，应替换为同一 API。`pages.py` 的 `select_item()` 可以稍后迁移为 modal reducer；不要在主 prompt 接入前重写 page mode。

第九步删除或收缩旧三层后端。`ui/cli/input.py` 不应再包含三套行为实现。最终它可以变成兼容导出层，只暴露 `prompt_async()`、`read_line_async()` 等窄接口，并在内部调用新的 prompt_input 包。保留非交互管道读取作为 batch mode 是允许的，但它不是交互后端，不承担补全、历史或行编辑。

第十步更新文档。修改 `docs/design-docs/cli-architecture.md`，把 `input.py` 的职责改为入口兼容层，把 `ui/cli/prompt_input/` 描述为状态机、建议、渲染和 driver 的所在地。补充说明旧轻量计划已被本 ExecPlan 取代，三层后端不再是目标架构。

## Concrete Steps

在仓库根目录 `D:\study\OneCode` 执行所有命令。

先建立新包和纯状态测试。建议按以下顺序创建文件：

    ui/cli/prompt_input/__init__.py
    ui/cli/prompt_input/state.py
    ui/cli/prompt_input/events.py
    ui/cli/prompt_input/editor.py
    ui/cli/prompt_input/reducer.py
    ui/cli/prompt_input/suggestions.py
    tests/test_cli_prompt_input_state.py
    tests/test_cli_prompt_input_suggestions.py

完成纯状态层后运行：

    uv run python -m pytest tests/test_cli_prompt_input_state.py tests/test_cli_prompt_input_suggestions.py -q

预期输出形态：

    ..                                                                     [100%]

实际测试数量会随实现增加而变化，成功标准是所有新增测试通过，没有 error 或 failure。

接入主 prompt 后运行：

    uv run python -m pytest tests/test_cli_commands.py tests/test_cli_prompt_input_state.py tests/test_cli_prompt_input_suggestions.py -q

如果仓库已有相关 CLI 输入测试，应一起运行。若新增了 `tests/test_cli_prompt_input_submission.py`，也加入命令。

完成结构性迁移后运行边界测试：

    uv run python -m pytest tests/test_import_boundaries.py -q

完成全部计划后运行更广泛测试：

    uv run python -m pytest tests -q

如果全量测试耗时或因外部环境失败，应在本文 `Surprises & Discoveries` 记录失败测试名、错误摘要和是否与本改动相关。

人工验证主 prompt：

    uv run python -m ui.cli.app

在启动后的 `onecode>` prompt 中输入 `abc`，按 Backspace，再按 Enter。若当前 provider 未配置，agent 调用可能失败；这不影响输入验收。可用一个 slash command 避免模型调用，例如输入 `/status` 验证命令路径。键入 `/` 后按 Tab 或触发建议，应能看到 `/status`、`/usage`、`/resume` 等命令候选。键入 `@` 后应能看到 workspace 当前层级文件候选。

## Validation and Acceptance

本计划完成时必须满足以下可观察行为。

第一，输入编辑一致。主 prompt 中输入 `abc`，按 Backspace，提交结果是 `ab`。在中间移动光标后插入或删除字符，提交文本反映光标位置，而不是总在末尾编辑。空输入下 Ctrl-C 抛出 `KeyboardInterrupt` 并让 CLI 按现有逻辑安全退出或回到主循环；空输入下 EOF 行为与现有 CLI 一致。

第二，建议统一。`/` 命令建议、`/resume ` session 建议和 `@` 文件建议都来自 `ui/cli/prompt_input/suggestions.py` 的同一候选结构。测试应证明 prompt-toolkit 兼容层和新 driver 不再各自实现候选生成。

第三，边界保持。`core/loop.py` 不因本计划增加 CLI 输入分支。`services/tools/`、`services/guard/`、`services/permissions/` 不依赖 `ui/cli/prompt_input/`。运行 `uv run python -m pytest tests/test_import_boundaries.py -q` 通过。

第四，非交互场景可用。管道或测试环境仍能通过简单 batch input 提交一行文本；这个路径不需要补全和历史，但不能破坏现有测试。

第五，敏感输入不回显。迁移 `/connect` 后，API key 输入仍不显示明文。应新增或保留测试覆盖 password mode 的不回显行为。

第六，文档同步。`docs/design-docs/cli-architecture.md` 不再描述三层后端作为目标设计，而描述新 PromptInput 状态机、driver、suggestion provider 和迁移边界。

## Idempotence and Recovery

本计划应通过 additive-first 的方式实施。先新增 `ui/cli/prompt_input/` 和测试，再把 `ui/cli/input.py` 改成兼容层，最后逐步删除旧实现。这样任一步失败时都可以回到仍可运行的旧主 prompt。

不要使用 `git reset --hard`、`git checkout --` 或批量删除旧文件来“回滚”。如果新 session 接入失败，恢复方式是把 `ui/cli/app.py` 中的主 prompt 调用临时切回旧 `prompt_async()`，保留新包和测试继续调试。

建议 provider 的文件候选必须保持有界。实现者不应把 `@file` 候选改成无界 `rglob("*")`，否则会触发大仓库性能问题，并与 `docs/tech-debt/tech-debt-tracker.md` 中 TD-020 的方向冲突。

长粘贴折叠在未设计 durable storage 前不要接入默认提交路径。如果实现了 prototype，必须能关闭，且不能丢失用户输入。

## Artifacts and Notes

本计划取代轻量计划中的目标架构，但不要求删除 `docs/exec-plans/active/cli-prompt-input-lightweight-plan.md`。实现者可以在完成第一阶段后选择把轻量计划移动到 completed 或在其顶部加注“已被本计划取代”，但这不是第一阶段必要条件。

参考目录 `docs/references/ui/components/PromptInput` 的可借鉴点如下。`inputModes.ts` 展示了模式前缀与真实值分离，例如 `!` 可表示 bash mode，但本计划暂不启用 bash 快捷模式。`inputPaste.ts` 展示了长粘贴可用占位符折叠，完整内容另存。`PromptInputFooterSuggestions.tsx` 展示了统一 suggestion item、可见窗口和宽度截断。`PromptInputQueuedCommands.tsx` 展示了后台通知限高和折叠。`PromptInput.tsx` 展示了集中仲裁 Enter、Esc、历史、建议、footer 和 modal 的方式。

旧 `ui/cli/input.py` 中可以复用的逻辑包括：`visible_commands()` 生成命令候选、`/resume` session id 候选、workspace 内当前路径层级的 `@file` 候选、`FileHistory` 路径 `.onecode/cli-history.txt`、`patch_stdout()` 对 streaming 输出的保护。不要照搬 `_read_line_with_key_reader()` 作为第二后端；它最多可被拆成某个 terminal driver 的按键读取辅助。

## Interfaces and Dependencies

最终应存在以下稳定接口。

在 `ui/cli/prompt_input/state.py` 中定义：

    @dataclass(frozen=True)
    class BufferState:
        text: str
        cursor: int

    @dataclass(frozen=True)
    class SuggestionItem:
        id: str
        kind: Literal["command", "file", "directory", "session"]
        display: str
        replacement: str
        description: str = ""

    @dataclass(frozen=True)
    class SuggestionState:
        items: tuple[SuggestionItem, ...] = ()
        selected: int = 0
        active: bool = False

    @dataclass(frozen=True)
    class PromptInputState:
        buffer: BufferState
        suggestions: SuggestionState
        mode: Literal["prompt"] = "prompt"
        is_password: bool = False

    @dataclass(frozen=True)
    class PromptSubmission:
        text: str
        kind: Literal["prompt", "command"]

具体字段可以扩展，但这些最小字段必须存在，除非实现者先更新本文 Decision Log 解释替代设计。

在 `ui/cli/prompt_input/editor.py` 中定义纯函数：

    insert_text(buffer: BufferState, text: str) -> BufferState
    delete_before_cursor(buffer: BufferState) -> BufferState
    delete_at_cursor(buffer: BufferState) -> BufferState
    move_left(buffer: BufferState) -> BufferState
    move_right(buffer: BufferState) -> BufferState
    move_home(buffer: BufferState) -> BufferState
    move_end(buffer: BufferState) -> BufferState
    clear_buffer(buffer: BufferState) -> BufferState
    delete_previous_word(buffer: BufferState) -> BufferState

在 `ui/cli/prompt_input/suggestions.py` 中定义：

    suggestions_for(runtime: CliRuntime, text: str, cursor: int) -> tuple[SuggestionItem, ...]

这个模块可以导入 `ui.cli.commands.visible_commands` 和 `CliRuntime` 类型，但不能导入 `core.loop`、具体工具目录或 provider adapter。

在 `ui/cli/prompt_input/session.py` 中定义：

    async def read_prompt(runtime_provider: Callable[[], CliRuntime]) -> PromptSubmission

如果为了兼容旧调用保留 `ui/cli/input.py.prompt_async() -> str`，它应委托到 `read_prompt()` 并返回 `submission.text`。当 `main_loop_async()` 改为直接消费 `PromptSubmission` 后，可以继续保留 `prompt_async()` 给 connect/permission 等旧入口过渡。

## Change Notes

2026-06-10 / Codex: 新建本 ExecPlan。原因是用户要求对 CLI 输入与交互进行彻底重构，并明确不保留当前三层后端架构；现有轻量计划不是完整 `PLANS.md` ExecPlan，且设计方向与新目标冲突。

2026-06-10 / Codex: 完成第一阶段纯状态机和 suggestion provider，并让旧 prompt-toolkit completer 消费 `SuggestionItem`。原因是先把候选生成事实来源抽离出来，可以在不破坏现有主 prompt 的前提下验证命令、`/resume` 和 `@file` 建议行为。

2026-06-10 / Codex: 接入 `prompt_input.session.read_prompt()` 到主 CLI loop，并让 Windows editable fallback 通过 reducer 编辑。原因是主循环只需要消费 `PromptSubmission`，命令分发和 agent loop 调用边界可以保持不变；fallback 也不应再维护独立编辑语义。

2026-06-11 / Codex: 完成剩余输入入口迁移。`/resume` selector 与 `/connect` provider 选择使用 modal selector；connect 文本/API key、权限确认和 MCP trust prompt 复用 `ui.cli.input` 单行输入接口；新增 MCP trust prompt 和 connect password mode 回归测试。
