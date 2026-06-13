# 产品化轻量终端 UI 重构

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文件遵循仓库根目录的 `PLANS.md`。实现者只阅读本文件和当前工作树，也应该能够完成 OneCode CLI 的新 UI。


## Purpose / Big Picture

本计划把 OneCode 当前的标准库命令行界面升级为产品化的轻量终端 REPL。REPL 是 read-eval-print loop 的缩写，意思是用户输入一行，程序执行并把结果打印出来，然后继续等待下一行。升级完成后，用户启动 `uv run python -m ui.cli.app` 会看到带 Unicode 符号、分隔线和彩色状态的终端界面；输入 `/` 会看到 slash 命令补全和命令说明；输入 `/resume` 或 `@` 这类需要参数的位置会出现参数补全；`/status`、`/tasks`、`/mcp`、`/memory`、`/permissions`、`/usage` 会以稳定的 Rich 视图展示当前运行时状态。

这次重构不把 OneCode 变成全屏 TUI。TUI 在这里指常驻全屏、多面板、alt-screen、鼠标交互的终端应用。OneCode 仍保持增强 REPL：输出一次视图后回到输入行。参考 Claude Code 的命令信息架构、状态语言、消息/工具事件分层和设计系统思想，但不复制它的 Ink、React reconciler、Yoga/Flexbox 布局、终端 DOM、screen buffer、双缓冲 diff 或鼠标事件系统。


## Progress

- [x] (2026-06-09, Codex) 阅读 `PLANS.md`，确认 ExecPlan 必须自包含、可执行、带进度、发现、决策和验收。
- [x] (2026-06-09, Codex) 阅读现有轻量 UI 草稿、CLI 架构、技术债跟踪和 Claude Code 参考文件，确认重构应落在 `ui/cli/`，不能把 runtime 逻辑搬进 UI。
- [x] (2026-06-09, Codex) 根据用户确认更新产品边界：第一版采用带 Unicode 符号、分隔线和彩色状态的产品化终端；支持参数补全；`/permissions` 只读；`/mcp` 可以只读展示可调用工具；允许 Unicode 符号但禁止 emoji。
- [x] (2026-06-09, Codex) 根据用户追加要求修订命令重构边界：不保留旧 `handle_command()` 兼容 wrapper，调用点和测试都必须迁移到 `dispatch_command()`。
- [x] (2026-06-09, Codex) 增加 `rich` 和 `prompt-toolkit` 依赖，运行 `uv sync --dev` 更新环境和 `uv.lock`，并确认 `uv run python -c "import rich, prompt_toolkit; print('ui deps ok')"` 通过。
- [x] (2026-06-09, Codex) 建立 `CommandSpec` 命令注册表，新增 `dispatch_command()`，删除旧 `handle_command()` 入口，并把 app、resume 测试和命令测试迁移到新入口。
- [x] (2026-06-09, Codex) 引入 CLI 主题和 Rich 视图层，覆盖 banner、错误、status、usage、memory、permissions、skills、MCP、tasks、history 和 compact 输出。
- [x] (2026-06-09, Codex) 引入 `prompt_toolkit` 输入循环、命令补全、`/resume` 参数补全、`@file` 补全、输入历史和 `patch_stdout()`；非 TTY 环境回退到线程 `input()`，以支持 Windows pytest/captured output。
- [x] (2026-06-09, Codex) 新增只读命令 `/skills`、`/memory`、`/permissions`、`/usage`，并为 `SessionPermissionStore` 增加只读 `snapshot()`。
- [x] (2026-06-09, Codex) 合并 `/tasks` 和后台任务视图，移除 `/background-tasks` 用户命令入口；`/quit`、`/help`、`/tools`、`/trace` 也改为 unknown command。
- [x] (2026-06-09, Codex) 更新 CLI 架构文档和测试；通过聚焦测试、compile check、import boundary 测试和全量测试。


## Surprises & Discoveries

- Observation: Claude Code 的 `/status` 和 `/usage` 不是临时字符串输出，而是进入同一个 Settings 视图的不同 tab。
  Evidence: `docs/references/ui/commands/status/status.tsx` 和 `docs/references/ui/commands/usage/usage.tsx` 都返回 `Settings` 组件，只是 `defaultTab` 不同。

- Observation: OneCode 已经有 `/status` 所需的大部分底层事实，不需要新增 runtime 状态系统。
  Evidence: `ui/cli/types.py` 的 `CliRuntime` 聚合了 `RuntimeState`、`MessageStore`、`ToolRegistry`、`McpConnectionManager`、`TaskStore`、`BackgroundTaskManager`、`SessionMemoryStore`、`LongTermMemoryStore`、`PermissionPolicy` 和 `HookRegistry`。

- Observation: OneCode MCP snapshot 已经包含只读工具展示所需字段。
  Evidence: `services/mcp/types.py` 定义 `McpConnectionSnapshot.statuses` 和 `McpConnectionSnapshot.tools`；`McpDiscoveredTool` 包含 `server_name`、`tool_name`、`descriptor_name`、`description` 和 `annotations`。

- Observation: `/permissions` 若要只读展示 session grant，不能直接读 `SessionPermissionStore` 私有字段。
  Evidence: `services/permissions/session.py` 当前只提供 `is_allowed()`、`is_tool_allowed()`、`is_tool_denied()` 等查询单项的方法，没有返回完整摘要的公共 snapshot 方法。

- Observation: 当前技术债已经把 CLI UI 不足列为活跃债务。
  Evidence: `docs/tech-debt/tech-debt-tracker.md` 的 TD-007 说明 CLI 缺少恢复 UI 和实时 trace 订阅，TD-016 说明附件系统缺少 CLI 可视化渲染。本计划缓解 CLI 呈现层，但不实现实时 trace 订阅，也不把附件可视化作为第一批必做项。

- Observation: `prompt_toolkit` 在 Windows pytest 捕获输出环境中会因为没有真实 console 而抛 `NoConsoleScreenBufferError`。
  Evidence: `tests/test_async_cli_streaming.py` 首次运行时在 `PromptSession()` 初始化阶段失败；`ui/cli/input.py` 现在仅在非 TTY 环境回退到 `asyncio.to_thread(input, "onecode> ")`，交互终端仍使用 `prompt_toolkit`。


## Decision Log

- Decision: 保持增强 REPL，不引入全屏 TUI、Textual、Ink clone 或 React terminal renderer。
  Rationale: OneCode 当前目标是把已有 runtime 状态清晰展示出来，而不是构建新的终端渲染引擎。Rich 和 prompt_toolkit 已足够支持彩色视图、分隔线、表格、补全和稳定输入体验。
  Date/Author: 2026-06-09 / Codex

- Decision: 第一版 UI 使用 Unicode 符号，但不使用 emoji，也不使用容易被终端渲染成 emoji 的符号作为核心状态标记。
  Rationale: 用户明确允许 Unicode 符号但禁止 emoji。状态符号使用 `✓`、`✗`、`!`、`i`、`○`、`…`、`›` 这类文本符号；避免依赖彩色 emoji 风格的图标。
  Date/Author: 2026-06-09 / Codex

- Decision: `/permissions` 只读展示，不提供交互式修改。
  Rationale: 权限策略是安全边界。第一版 UI 只消费 `PermissionPolicy`、`SessionPermissionStore` 和 `ProjectPermissionSettingsStore` 的状态，不新增修改入口，避免 UI 重构改变权限语义。
  Date/Author: 2026-06-09 / Codex

- Decision: `/mcp` 可以展示 MCP 可调用工具，但展示是只读的，并且不提供启用、禁用、编辑、重连或授权操作。
  Rationale: 用户希望知道 MCP 能调用什么工具。OneCode 已有 `McpConnectionSnapshot.tools`，所以可以安全展示工具名、server、description 和 annotation 摘要。修改 MCP 配置仍留给配置文件和既有 trust 流程。
  Date/Author: 2026-06-09 / Codex

- Decision: 删除 `/quit`、`/help`、`/tools`、`/trace`、`/background-tasks` 这些用户可见入口，保留底层能力。
  Rationale: `/exit` 足够表达退出；命令说明由补全和预览承担；工具和 trace 是开发者诊断，不应作为普通用户默认命令；后台任务应合并进 `/tasks`。删除命令不删除 `TraceRecorder`、`ToolRegistry`、`BackgroundTaskManager` 或 MCP descriptor。
  Date/Author: 2026-06-09 / Codex

- Decision: 命令重构后不保留 `handle_command()` 函数，所有调用点统一改为 `dispatch_command()`。
  Rationale: 兼容 wrapper 会让新旧命令分发长期并存，削弱 `CommandSpec` registry 作为唯一事实来源的目标。测试应随实现一起迁移，不用旧 API 保护旧断言。
  Date/Author: 2026-06-09 / Codex

- Decision: `prompt_toolkit` 输入在非 TTY 环境回退到标准 `input()`，但补全器继续作为独立类单元测试。
  Rationale: Windows 的 captured stdout/stdin 不提供真实 console buffer；强行创建 `PromptSession` 会让非交互测试崩溃。回退只影响测试、管道或重定向环境，不改变真实终端中的补全、history 和 `patch_stdout()` 行为。
  Date/Author: 2026-06-09 / Codex


## Outcomes & Retrospective

2026-06-09 / Codex: 第一轮实现完成。CLI 现在使用 `CommandSpec` 注册表和 `dispatch_command()`，Rich banner/状态视图、`prompt_toolkit` 输入补全、只读 `/skills`/`/memory`/`/permissions`/`/usage`、MCP 工具只读展示和合并后的 `/tasks` 均已落地。删除的用户命令 `/quit`、`/help`、`/tools`、`/trace`、`/background-tasks` 会返回 unknown command。验证通过：`uv run python -c "import rich, prompt_toolkit; print('ui deps ok')"`、`uv run python -m compileall ui services`、`uv run python -m pytest tests/test_cli_commands.py tests/test_cli_resume.py tests/test_cli_completion.py -q`、`uv run python -m pytest tests/test_async_cli_streaming.py -q`、`uv run python -m pytest tests/test_import_boundaries.py -q`、`uv run python -m pytest tests -q`。


## Context and Orientation

OneCode 是 Python code agent runtime。核心 agent 主循环在 `core/loop.py`，上下文治理在 `services/context/`、`services/compaction/` 和 `services/memory/`，工具执行在 `services/tools/` 和 `tools/`，权限在 `services/permissions/`，MCP 在 `services/mcp/`，任务在 `services/tasks/` 和 `services/background_tasks/`。本计划只重构 `ui/cli/` 的用户界面，不改变 agent 主循环、工具执行、安全策略或 provider 协议。

当前 CLI 入口是 `ui/cli/app.py`。`build_runtime(workspace)` 创建 `CliRuntime`，`main_loop_async(runtime)` 打印 Rich banner，使用 `ui/cli/input.py` 的 `prompt_async()` 等待一行输入。如果输入以 `/` 开头，它调用 `ui/cli/commands.py` 的 `dispatch_command()`；否则收集 `@mention` 附件，然后调用 `AgentLoop.stream()` 并打印 assistant delta 和工具结果摘要。

`ui/cli/commands.py` 当前用一串 `if command == ...` 分支处理 slash 命令。slash command 是以 `/` 开头的用户命令，例如 `/status` 或 `/resume session-id`。当前命令分发、帮助文本、banner 中列出的命令和测试里的预期彼此分散，导致删除或新增命令时容易漏改。

`ui/cli/renderer.py` 当前返回纯字符串。它负责 banner、help、status、tasks、background tasks、MCP、history、trace、compact 结果和错误文本。重构后，renderer 应逐步返回 Rich renderable。Rich renderable 是 Rich 库可以打印的对象，例如 `Panel`、`Table`、`Text`、`Markdown`、`Syntax` 或普通字符串。测试仍应能用无颜色的文本输出来断言行为。

`ui/cli/types.py` 定义 `CliRuntime` 和 `CommandResult`。`CliRuntime` 是 CLI 可见状态容器，已经持有 runtime、store、registry、MCP manager、task manager、memory store 和 permission policy。本计划继续使用 `CliRuntime` 作为 UI 读取状态的入口，不新增全局 UI store。

Claude Code 参考文件放在 `docs/references/ui/`。可借鉴的文件包括 `commands/*/index.ts` 的命令元数据、`components/App.tsx` 的 provider 分层、`screens/REPL.tsx` 的输入/消息/权限/任务区域分离、`design-system/StatusIcon.tsx` 的状态枚举、`design-system/ListItem.tsx` 的选择项语义、`components/Markdown.tsx` 的 streaming markdown 性能思路、`components/CompactSummary.tsx` 的 compact 摘要视图、`components/FileEditToolDiff.tsx` 的 diff panel 边界、`components/mcp/MCPListPanel.tsx` 和 `MCPToolListView.tsx` 的 MCP 分组与状态字段。不可借鉴为实现目标的是 `ink/` 下的自定义 terminal renderer、React reconciler、Yoga layout、screen buffer 和 diff engine。


## Plan of Work

第一步是建立命令注册表。新增或改写 `ui/cli/commands.py`，定义 `CommandSpec` 和 `CommandInvocation`。`CommandSpec` 是一个不可变数据对象，包含命令事实：命令名、参数提示、描述、分类、别名、是否可见、是否立即执行、handler 和可选参数补全器。命令名采用不带 `/` 的形式，例如 `status`，显示时再渲染为 `/status`。这样可以更接近 Claude Code 的命令模型，同时避免在内部数据里到处拼接斜杠。

在 `ui/cli/types.py` 扩展 `CommandResult`，让命令 handler 可以返回 `renderable`。`renderable` 可以是字符串或 Rich renderable。保留 `should_exit` 和 `runtime`，因为 `/exit` 需要退出，`/clear` 和 `/resume` 会返回新的 `CliRuntime`。不要保留 `handle_command(runtime, line)` 作为兼容层；`ui/cli/app.py`、补全测试、命令测试和任何内部调用点都要直接使用 `dispatch_command(runtime, line)`。命令结果由统一打印函数把 `CommandResult.renderable` 输出到 Rich `Console`。

命令注册表第一批保留 `/status`、`/tasks`、`/mcp`、`/history`、`/compact`、`/resume`、`/clear`、`/exit`。删除 `/quit`、`/help`、`/tools`、`/trace`、`/background-tasks` 的用户入口。未知命令的错误文案改成类似 `Unknown command: /foo. Press Tab after / to see available commands.`，不要再提示 `/help`。删除命令时只删除用户入口，不删除底层 recorder、registry、manager 或 helper。

第二步是增加主题和 Rich 视图层。新增 `ui/cli/theme.py`，定义 `CliTheme` 或简单常量表。主题至少包含 style 名称和状态符号。状态符号建议为 `success="✓"`、`error="✗"`、`warning="!"`、`info="i"`、`pending="○"`、`loading="…"`、`pointer="›"`。这些是 Unicode 文本符号，不是 emoji。style 名称至少包含 `onecode.title`、`onecode.subtle`、`onecode.command`、`onecode.path`、`onecode.success`、`onecode.error`、`onecode.warning`、`onecode.info`、`onecode.permission`、`onecode.model`、`onecode.session` 和 `onecode.metric`。

新增 `ui/cli/views/` 目录，用专门模块承载视图。建议文件为 `status.py`、`tasks.py`、`mcp.py`、`memory.py`、`permissions.py`、`skills.py`、`usage.py`、`history.py`、`common.py`。视图函数只读取 `CliRuntime` 和传入数据，返回 Rich renderable，不执行 runtime 操作。`common.py` 提供状态 badge、相对路径显示、空状态 panel、短文本预览、无颜色字符串渲染 helper。

`ui/cli/renderer.py` 在迁移期间可以保留旧函数名，但它应委托给 `views/`。测试需要纯文本时，使用 `Console(record=True, force_terminal=False, color_system=None)` 把 Rich renderable 转成文本。这样既能产品化终端，也能让单元测试稳定断言。

第三步是把 banner、错误和核心状态视图改为 Rich。banner 用 `Panel` 或清晰分隔线展示产品名、workspace、session、provider/model 和可用命令提示。错误用统一 `render_error()`，带 `✗` 符号和红色 style。`/status` 是总览，显示 workspace、session、provider、model、turns、last transition、usage 摘要、transcript/error log 路径、MCP 概览、background task 概览、memory 概览和 compaction 概览。`/usage` 从 `/status` 中拆出 token、turn、cache、compaction threshold 和最近 compact 信息。`/memory` 展示 session memory 文件、更新时间、long-term memory 目录、topic 数、最近抽取状态和最近 surfaced memory。`/permissions` 展示 session grant 和 project rule 摘要，但不提供修改动作。

`/permissions` 需要只读状态快照。不要从 UI 直接访问 `SessionPermissionStore._allowed_directories` 等私有字段。应在 `services/permissions/session.py` 增加一个只读方法，例如 `snapshot()`，返回一个 frozen dataclass 或普通 dict，包含 allowed directories、allowed tools、allowed skills、denied skills、denied tools、disabled tools。这个方法只暴露数据，不改变权限判定。project rule 可通过 `runtime.permission_policy.project_store.load_rules()` 读取；若没有 project store，视图显示 `project rules: disabled` 或 `none`。

第四步是改造 `/tasks`。Durable task 是 `services/tasks/` 中持久化到文件的跨回合任务；background task 是 `services/background_tasks/` 中进程内运行的后台 bash、agent 或 dream。用户只需要一个入口知道“正在发生什么”，所以 `/tasks` 应同时显示 durable tasks 和 background tasks。原 `/background-tasks` 命令删除，`render_background_tasks()` 可以保留为内部 helper，但不能注册为用户命令。

第五步是改造 `/mcp`。MCP 是 Model Context Protocol，用于让 OneCode 连接外部 server 并发现外部工具。`/mcp` 应显示 server 表和只读工具清单。server 表读取 `runtime.mcp_manager.snapshot().statuses`，列出 name、transport、state、tool_count、instructions_present 和 error 摘要。工具清单读取 `snapshot.tools`，按 `server_name` 分组，显示 `tool_name`、`descriptor_name`、description 预览和 annotations 摘要。这个视图不提供启用、禁用、编辑、重连、trust 或删除操作。若工具很多，第一版可以全部显示；若后续出现可用性问题，再在本计划的 `Decision Log` 中记录是否加截断规则。

第六步是引入 `prompt_toolkit`。新增 `ui/cli/input.py`，用 `PromptSession.prompt_async()` 替换 `app.py` 中的 `asyncio.to_thread(input, "onecode> ")`。使用 `FileHistory` 保存输入历史，例如 `.onecode/cli-history.txt`。使用 `patch_stdout()` 包裹等待输入的上下文，避免 streaming 输出打乱当前输入行。`prompt_toolkit` 是 Python 终端输入库，负责可编辑输入、history、completion 和 display_meta；它不替代 Rich 渲染。

补全器定义在 `ui/cli/input.py` 或 `ui/cli/completion.py`。命令补全读取 `CommandSpec` registry。输入 `/` 或 `/sta` 时补全 `/status`，并用 `display_meta` 显示命令描述和参数提示。参数补全第一版必须支持 `/resume` 和 `@file`。`/resume` 补全 `.onecode/<session-id>/messages.jsonl` 对应的 session id，也允许补全明确的 `.jsonl` 路径。`@file` 补全 workspace 内文件和目录，优先使用 bounded traversal 或 `Path.iterdir()` 逐层补全，避免对大仓库无界扫描。`/history` 可以提供常用数字如 `10`、`20`、`50` 的提示，但不是硬要求。`/compact` 的 focus 是自由文本，不做复杂补全。

第七步是新增只读命令。`/skills` 读取 `runtime.skill_provider.visible_skills(runtime.state, runtime.workspace)`，展示 name、source、description、paths 和 allowed tools 摘要。`/memory`、`/permissions`、`/usage` 按前文视图实现。`/hooks`、`/context`、`/export`、`/recap` 暂不做，除非本计划后续修订明确纳入。

第八步是更新测试和文档。`tests/test_cli_commands.py` 要改写旧命令预期：不再断言 `/help`、`/tools`、`/trace`、`/background-tasks` 是有效用户命令；断言这些命令会返回 unknown command；断言 `/quit` 不再退出；断言 `/mcp` 同时展示 server 和工具只读列表；断言 `/tasks` 同时展示 durable tasks 和 background tasks；断言 `/permissions` 不修改 stores。新增 `tests/test_cli_completion.py` 或在现有 CLI 测试中覆盖 command completion、`/resume` 参数补全和 `@file` 补全。更新 `docs/design-docs/cli-architecture.md`，说明新文件职责、命令注册表、Rich 视图、prompt_toolkit 输入和只读状态命令。


## Concrete Steps

从仓库根目录 `D:\study\OneCode` 开始。先确认当前工作树，避免覆盖无关用户改动：

    git status --short

如果看到与本计划无关的修改，不要回滚。继续只编辑本计划涉及的文件，并在最终总结中说明测试是在脏工作树上运行。

添加依赖。编辑 `pyproject.toml` 的 `[project].dependencies`，加入：

    "rich>=13.7.0",
    "prompt-toolkit>=3.0.43",

然后同步环境：

    uv sync --dev

如果因为网络或索引访问失败，按当前执行环境的权限规则重新请求网络授权后再运行。成功后验证导入：

    uv run python -c "import rich, prompt_toolkit; print('ui deps ok')"

预期输出：

    ui deps ok

实现命令注册表。编辑 `ui/cli/types.py`，扩展 `CommandResult`。编辑 `ui/cli/commands.py`，新增 `CommandSpec`、`CommandInvocation`、`command_registry()`、`visible_commands()`、`dispatch_command()`。删除旧 `handle_command()` 函数，不新增兼容 wrapper。实现时保持 `/clear`、`/resume`、`/compact` 和 `/exit` 的原有 side effect：flush transcript/trace/error log、重建 session runtime、关闭 MCP 等行为不能丢失。

建立主题和视图目录。新增 `ui/cli/theme.py`、`ui/cli/views/__init__.py` 和 `ui/cli/views/common.py`。随后逐个迁移视图。先迁移 banner 和 error，因为它们简单且每次启动都会被用户看到。再迁移 `/status`、`/usage`、`/memory`、`/permissions`、`/skills`、`/mcp` 和 `/tasks`。

实现 `SessionPermissionStore.snapshot()`。编辑 `services/permissions/session.py`，新增只读 snapshot 方法，不改变现有字段写入方式。返回值应复制内部集合，避免调用方能修改 store 内部状态。可以定义 frozen dataclass `SessionPermissionSnapshot`，也可以返回只含 tuple 的 dict。推荐 dataclass，因为测试更清晰。

实现 prompt_toolkit 输入。新增 `ui/cli/input.py`，定义一个小类或函数，例如 `create_prompt_session(runtime)` 和 `prompt_user(runtime) -> str`。`main_loop_async()` 中把 `await asyncio.to_thread(input, "onecode> ")` 替换为该函数。注意 `runtime` 在 `/clear` 或 `/resume` 后会变化，所以输入层不要永久缓存旧 runtime；每次 prompt 前应能读取当前 registry 和 workspace。

更新测试。先运行聚焦测试，观察失败，然后按新行为改写：

    uv run python -m pytest tests/test_cli_commands.py -q

预期在迁移中会先失败，最终应通过。补全测试可单独运行：

    uv run python -m pytest tests/test_cli_completion.py -q

如果没有单独文件，也可以把补全测试放入 `tests/test_cli_commands.py`，但测试名称要明确包含 `completion`。

最后运行编译和边界测试：

    uv run python -m compileall ui services
    uv run python -m pytest tests/test_import_boundaries.py -q
    uv run python -m pytest tests/test_async_cli_streaming.py -q

完成后手动启动 CLI：

    uv run python -m ui.cli.app

手动输入以下命令观察行为：

    /
    /status
    /usage
    /memory
    /permissions
    /skills
    /mcp
    /tasks
    /help
    /exit

`/` 应出现补全菜单。`/status` 等视图应有彩色状态、分隔线和 Unicode 文本符号。`/mcp` 应展示 server 和只读工具清单。`/permissions` 应只展示状态，不询问、不写文件、不改变权限 store。`/help` 应是未知命令，并提示用 Tab 查看命令。`/exit` 应正常退出。


## Validation and Acceptance

实现完成时，以下行为必须成立。

启动 `uv run python -m ui.cli.app` 后，banner 不是纯旧文本列表，而是产品化 Rich 输出。即使在无颜色测试环境中，文本仍应包含 OneCode、workspace、session、provider/model 和命令发现提示。不能包含 emoji。

输入 `/` 或 `/sta` 后，prompt_toolkit 补全菜单显示可见命令。每个命令有描述。`/help` 不再是发现命令的主要方式。

输入 `/resume ` 后，补全器提供已有 `.onecode/<session-id>/messages.jsonl` 对应的 session id 或 JSONL 路径候选。输入普通 prompt 时，在 `@` 后能补全 workspace 内文件或目录。补全不得要求全屏 TUI。

输入 `/status` 后，用户能看到 workspace、session、provider/model、turn usage、last transition、transcript path、error log path、MCP 概览、background task 概览、memory 概览和 compaction 概览。

输入 `/usage` 后，用户能看到 input/output/cache token、turn count、max turns、auto compact threshold 和最近 compact 信息。

输入 `/memory` 后，用户能看到 session memory 文件路径、是否存在、更新时间、long-term memory 目录、topic 数和最近 extraction 状态。

输入 `/permissions` 后，用户能看到 session grants、denied/disabled tools or skills、project settings path 和 project allow/deny/ask rules。这个命令不能调用 `record_response()`，不能写 `.onecode/settings.json`，不能改变 session permission store。

输入 `/mcp` 后，用户能看到 MCP server 状态和可调用工具清单。工具展示只读，不出现修改、启用、禁用、删除、trust、reconnect 的交互。

输入 `/tasks` 后，用户能同时看到 durable tasks 和 background tasks。`/background-tasks` 不再是有效命令。

输入 `/quit`、`/help`、`/tools`、`/trace`、`/background-tasks` 后，CLI 显示 unknown command，并提示用 Tab 查看命令。底层 `TraceRecorder`、`ToolRegistry`、`BackgroundTaskManager` 和 MCP descriptors 仍保留给 runtime 和测试使用。

测试命令应通过：

    uv run python -m pytest tests/test_cli_commands.py -q
    uv run python -m pytest tests/test_cli_completion.py -q
    uv run python -m pytest tests/test_async_cli_streaming.py -q
    uv run python -m pytest tests/test_import_boundaries.py -q
    uv run python -m compileall ui services

如果 `tests/test_cli_completion.py` 没有独立文件，则在最终记录中说明补全测试位于 `tests/test_cli_commands.py` 的哪些测试名下。


## Idempotence and Recovery

本计划的代码改动应是幂等的。重复运行 `uv sync --dev`、pytest 和 compileall 不应改变工作树，除非工具本身更新 lock 或 cache。若 `uv sync --dev` 修改 lock 文件，保留该修改并在最终总结中说明它来自新增依赖。

`/clear` 和 `/resume` 是 session 切换命令，必须继续使用 `CliRuntime.with_session()` 重建 session scoped 组件。不要手动拼接部分状态，也不要在 UI 层复制 runtime 装配逻辑。如果 `/resume` 失败，应只显示错误，不改变当前 runtime。

`/permissions` 只能读状态。若实现 snapshot 时发现需要暴露更多 store 数据，优先新增只读 public method，不要让 view 读取私有字段。如果 project settings JSON 无效，视图应显示可读错误；不要吞掉异常后显示错误规则。

`/mcp` 工具展示只读。不要在 UI 中调用 `connect_all_blocking()`、`ensure_connected()`、`close_all()` 或任何会改变 server 状态的方法。读取 `snapshot()` 即可。

如果 Rich 渲染在测试中包含 ANSI 颜色导致断言不稳定，用 `Console(record=True, force_terminal=False, color_system=None)` 生成无 ANSI 文本。不要把测试改成只断言“没有抛异常”；必须断言用户可见关键文本。

如果 prompt_toolkit 在非交互测试环境中难以直接运行，不要跳过输入体验测试。把 completer 单独做成可实例化类，对 `Document` 或等价输入对象做单元测试，验证候选命令、候选参数和 display metadata。


## Artifacts and Notes

参考输出示例只表达语义，不要求逐字符一致。颜色在真实终端中由 Rich 样式呈现，测试可断言无颜色文本。

启动 banner 的无颜色文本应类似：

    OneCode
    workspace  D:\study\OneCode
    session    20260609-...
    model      provider / model-name
    Type / and press Tab to browse commands.

未知命令应类似：

    ✗ Unknown command: /help. Press Tab after / to see available commands.

`/mcp` 应类似：

    MCP
    Servers
      ✓ filesystem  stdio  connected  tools=3  instructions=yes
      ✗ remote-api  http   failed     tools=0  error=...

    Tools
      filesystem
        read_file       descriptor=mcp_filesystem_read_file
        list_directory  descriptor=mcp_filesystem_list_directory

`/permissions` 应类似：

    Permissions
    Session
      allowed directories: 2
      allowed tools: bash
      denied tools: none

    Project
      settings: .onecode/settings.json
      allow: read_file:*
      deny: bash:rm *
      ask: none

状态符号必须是文本符号，不是 emoji。推荐符号集：

    success  ✓
    error    ✗
    warning  !
    info     i
    pending  ○
    loading  …
    pointer  ›


## Interfaces and Dependencies

`pyproject.toml` 必须包含：

    "rich>=13.7.0",
    "prompt-toolkit>=3.0.43",

`ui/cli/types.py` 中的 `CommandResult` 在计划结束时应至少支持：

    @dataclass(frozen=True)
    class CommandResult:
        should_exit: bool = False
        runtime: CliRuntime | None = None
        renderable: object | None = None

如果实现者选择不冻结 dataclass，也必须保持字段语义一致。`renderable` 的类型可以后续收窄为 Rich 的 `RenderableType`，但不要让 core 或 services 依赖 Rich。

`ui/cli/commands.py` 中应存在：

    @dataclass(frozen=True)
    class CommandInvocation:
        raw: str
        name: str
        args: tuple[str, ...]
        arg_text: str

    @dataclass(frozen=True)
    class CommandSpec:
        name: str
        description: str
        argument_hint: str = ""
        category: str = "general"
        aliases: tuple[str, ...] = ()
        visible: bool = True
        handler: Callable[[CliRuntime, CommandInvocation], CommandResult]
        parameter_completer: Callable[[CliRuntime, str], Iterable[str]] | None = None

`command_registry()` 返回所有命令的 tuple 或 mapping。`visible_commands()` 只返回 `visible=True` 的命令。`dispatch_command(runtime, line)` 解析输入并返回 `CommandResult`。计划完成后 `ui/cli/commands.py` 不应再导出 `handle_command()`；旧测试和简单调用必须迁移到 `dispatch_command()`。

`ui/cli/theme.py` 应暴露主题常量，至少包含状态符号和 Rich style。不要在主题中读取 runtime，也不要把颜色散落到各个 view。

`ui/cli/input.py` 应暴露一个 prompt_toolkit 输入入口和一个可单测 completer。推荐接口：

    class OneCodeCompleter(Completer):
        def __init__(self, runtime_provider: Callable[[], CliRuntime]) -> None: ...

    async def prompt_async(runtime_provider: Callable[[], CliRuntime]) -> str: ...

这里 `runtime_provider` 是返回当前 `CliRuntime` 的函数。这样 `/clear` 和 `/resume` 替换 runtime 后，补全器仍能读取最新 session 和 workspace。

`services/permissions/session.py` 应新增只读 snapshot。推荐接口：

    @dataclass(frozen=True)
    class SessionPermissionSnapshot:
        allowed_directories: tuple[tuple[str, str, Path], ...]
        allowed_tools: tuple[str, ...]
        allowed_skills: tuple[str, ...]
        denied_skills: tuple[str, ...]
        denied_tools: tuple[str, ...]
        disabled_tools: tuple[str, ...]

    class SessionPermissionStore:
        def snapshot(self) -> SessionPermissionSnapshot: ...

返回值必须排序或在测试中稳定化，避免 set 顺序导致断言抖动。

`ui/cli/views/mcp.py` 只读取 `McpConnectionSnapshot`，不得调用会改变连接状态的方法。`ui/cli/views/permissions.py` 只读取 `SessionPermissionStore.snapshot()` 和 `ProjectPermissionSettingsStore.load_rules()`，不得调用 `apply_update()` 或 `PermissionPolicy.record_response()`。

`docs/design-docs/cli-architecture.md` 必须在实现完成后更新，说明新的职责划分：

`app.py` 负责 runtime 装配和主循环；`input.py` 负责 prompt_toolkit 输入、历史和补全；`commands.py` 负责命令注册和分发；`theme.py` 负责 CLI 样式和符号；`renderer.py` 负责兼容和 Rich console 输出；`views/` 负责用户可见状态视图；`permissions.py` 继续只负责工具执行前的权限确认面板，不被 `/permissions` 只读视图替代。


### User-Facing Scope

启动主页只显示具体信息，不显示字段说明，也不显示模型来源。主页应保留 `OneCode`、当前 workspace 绝对路径和当前模型名。示例：

    OneCode
    D:\study\OneCode
    deepseek-chat

主页不显示 `workspace`、`session`、`provider`、`model` 这些标签，不显示 `DeepSeek / deepseek-chat` 这种来源加模型组合。模型名使用 `runtime.model`。

`/status`、`/usage`、`/mcp`、`/tasks`、`/permissions`、`/memory`、`/skills` 进入只读 page 模式。page 模式下主 prompt 失活，用户只能按 `Esc` 退出，允许滚动键查看长内容。普通字符、Enter、slash command 和其他按键都必须被忽略，不能进入输入缓冲，也不能在退出页面后作为命令执行。

`/history` 不作为用户命令实现。历史查看只通过 `/resume` 进入：用户先选择会话，按 Enter 恢复到该会话状态，然后自动进入该会话的只读历史页面。按 `Esc` 退出历史页面后，prompt 继续在已恢复的会话中工作。

`/connect` 进入连接向导。用户用方向键从上到下选择 provider，最后一项是 `Custom`。选择 provider 后按 Enter 进入配置输入。非 custom provider 提示输入 API key，再输入 model；custom provider 先输入 base URL，再输入 API key，再输入 model。写入项目 `.env` 后重建当前 runtime 的模型相关组件。

### Interaction Modes

第一版可用 `prompt_toolkit` 的 `PromptSession`、key bindings、completion menu 和受控 prompt 完成这些模式，不要求实现自定义终端 screen buffer。若 `prompt_toolkit` 无法可靠承载 page/picker 模式，再评估是否引入更小的 raw-key helper，但不要把核心 runtime 逻辑搬进 UI。

### Backspace Fix

当前启动后输入命令无法按退格键的问题应作为输入层 bug 单独修复。优先检查 `ui/cli/input.py` 的 TTY 判定和 Windows 控制台行为：当前实现只要 `stdin` 或 `stdout` 任一不是 TTY 就回退到标准 `input()`，这在集成终端和测试捕获环境里可能导致退格表现不一致。

修复方向：

1. 真实交互终端优先使用 `prompt_toolkit`。
2. 非交互测试、pipe 和 captured output 继续使用 fallback，保证现有 pytest 不需要真实 console。
3. 为 `prompt_toolkit` 输入显式绑定 Backspace 相关按键：`backspace`、`c-h`、`delete`，确保 `/abc` 可以退格到 `/ab`、`/a`、`/` 和空输入。
4. 增加输入层测试，至少覆盖 completer 不被 Backspace 状态破坏；如能稳定模拟 key binding，则覆盖 Backspace 删除行为。

### Command Preview And Completion

命令发现继续以 `CommandSpec` 为唯一事实来源。输入 `/` 时展示所有可见命令，输入 `/a` 时过滤匹配命令。补全候选应接近参考截图的两列语义：左侧命令名，右侧描述和参数 hint。第一版可以使用 `prompt_toolkit` 的 completion display metadata；若默认菜单太弱，再自定义 completion display，但不新增 `/help`。

候选中不要出现已删除或不希望暴露的用户命令。`/history` 不出现。`/connect`、`/resume`、`/status`、`/usage`、`/mcp`、`/tasks`、`/permissions`、`/memory`、`/skills`、`/compact`、`/clear`、`/exit` 应可发现。

### Page Commands

只读 page 命令第一批为：

    /status
    /usage
    /mcp
    /tasks
    /permissions
    /memory
    /skills

这些命令返回页面内容后，主输入阻断。页面页脚应提示：

    Esc return

如果内容超过终端高度，允许滚动键。滚动键只影响页面视口，不进入 prompt。Enter 和普通字符忽略。

动作命令仍保持 inline 行为：

    /compact
    /clear
    /exit

`/compact` 会修改上下文，不进入只读页面。`/clear` 和 `/exit` 是状态切换或退出动作，不进入页面。

### Resume Picker And Session History

`/resume` 不再主要依赖用户手动输入 session id。输入 `/resume` 后进入 picker 模式，扫描 `.onecode/*/messages.jsonl`，列出可恢复会话。每行展示一个小标题、最后更新时间和消息数量。示例：

    Resume

    > 修复 CLI 输入退格问题                  2026-06-09  18 messages
      新 UI 计划讨论                         2026-06-08  42 messages
      OneCode permission policy cleanup       2026-06-07  15 messages

    Esc cancel    Enter resume    Up/Down move

会话小标题第一版不新增模型生成。标题从 transcript 派生：

1. 读取第一条非空 `user` message。
2. 去掉换行、多余空白和明显 attachment/XML 噪音。
3. 截断到 40 到 60 个字符。
4. 若没有 user message，用第一条 assistant message。
5. 仍没有内容时，用短 session id。

按 Enter 选择后，复用现有恢复逻辑：`resolve_resume_target()`、`MessageStore.from_transcript()`、`runtime.with_session()`。恢复成功后立即展示该会话历史页面。历史页面按时间顺序展示恢复后的 `message_store.current_messages()`：

- `user`: 展示前几行或前 N 字符。
- `assistant`: 展示前几行或前 N 字符。
- `tool_result`: 默认折叠成 `tool_name call_id ok/error`，避免大工具输出刷屏。
- `attachment`: 展示 attachment 类型和路径摘要。

历史页面是只读 page 模式，按 `Esc` 回到 prompt。退出历史页后，当前 runtime 必须已经是恢复后的会话。

### Connect Wizard

新增 `/connect` 命令，进入 provider 连接向导。provider 选择页使用 picker 模式，顺序来自 `infrastructure.providers.connection.ProviderConnectionService.list_connect_options()`，并保持 `Custom` 最后一项。当前 provider catalog 顺序为：

    OpenAI
    DeepSeek
    GLM
    MiniMax
    SiliconFlow
    Gemini
    Claude OpenAI-compatible
    Custom

非 custom 流程：

1. 选择 provider，按 Enter。
2. 输入 API key，隐藏或掩码显示。
3. 输入 model，例如 `deepseek-chat`。
4. 写入 `.env`。
5. 重建当前 runtime 的模型相关组件。
6. 显示连接成功页面或 inline 成功结果。

custom 流程：

1. 选择 `Custom`，按 Enter。
2. 输入 base URL，例如 `https://example.com/v1`。
3. 输入 API key，隐藏或掩码显示。
4. 输入 model。
5. 写入 `.env`。
6. 重建当前 runtime 的模型相关组件。

`.env` 写入必须遵守现有 model provider 边界：OneCode 只从项目根目录 `.env` 读取 provider 配置。写入字段为：

    ONECODE_PROVIDER_ID=<provider-id>
    ONECODE_MODEL=<model>
    ONECODE_API_KEY=<secret>

custom 或需要 base URL 的 provider 额外写：

    ONECODE_BASE_URL=<base-url>

写入规则：

- `.env` 不存在时创建最小可用文件。
- `.env` 存在时保留无关字段和注释，只更新 OneCode provider 相关键。
- API key 不能出现在 console 输出、trace、error log 或异常详情中。
- 切换到不需要 custom base URL 的 provider 时，不允许旧 custom URL 污染新 provider。可以删除 `ONECODE_BASE_URL`，也可以写为空值；实现前在 Decision Log 记录选择。

写入 `.env` 后不能只更新 banner。必须重建当前 CLI runtime 的模型相关组件，使下一轮模型调用使用新连接。推荐在 `CliRuntime` 上新增 `with_provider_config()` 或 `with_model_client()`，由它重建：

- `model_client`
- `RelevantMemorySelector`
- `ContextEngine`
- `SubagentRunner`
- `SessionMemoryExtractionService`
- `LongTermMemoryExtractionService`
- `AgentLoop`

避免在 `/connect` handler 内复制 `build_runtime()` 的大段装配逻辑。`core/` 和 `services/model/` 仍只依赖 provider-neutral 协议；`.env` 写入和 provider catalog 读取属于 CLI 与 infrastructure 边界。

### Implementation Sketch

建议新增或调整这些文件：

- `ui/cli/pages.py`: page/picker/form 模式输入循环和键盘处理。
- `ui/cli/views/resume.py`: resume 会话列表和恢复后历史页面。
- `ui/cli/views/connect.py`: provider 选择、输入提示和连接结果视图。
- `ui/cli/resume.py`: 扫描 `.onecode/*/messages.jsonl`，生成 `SessionSummary`，派生小标题、更新时间、消息数。
- `ui/cli/connect.py`: provider 选项、`.env` 更新、连接结果对象。API key 处理必须避免日志泄漏。
- `ui/cli/types.py`: 扩展 `CommandResult`，支持 `presentation` 或专门的 page/picker action；增加 runtime model client 重绑定 helper。
- `ui/cli/commands.py`: 注册 `/connect`，移除 `/history` 用户入口，把 `/resume` 改为 picker action。
- `ui/cli/input.py`: 修复 Backspace，调整 command completion preview。
- `ui/cli/views/status.py`: 精简主页 banner，去掉 session、provider 和字段标签。

可以把页面结果抽象为：

    @dataclass(frozen=True)
    class CommandResult:
        should_exit: bool = False
        runtime: CliRuntime | None = None
        renderable: object | None = None
        presentation: Literal["inline", "page", "resume_picker", "connect_wizard"] = "inline"

也可以保持 `CommandResult` 简洁，新增 `CommandAction` 联合类型承载 picker/wizard。关键是 `main_loop_async()` 能明确区分 inline 输出、只读页面、会话选择和连接向导，不再把所有命令都当成“打印 renderable 后回 prompt”。

## Revision Note

2026-06-09 / Codex: 将原轻量讨论草稿升级为符合 `PLANS.md` 的中文 ExecPlan。修订原因是用户确认了产品化终端、参数补全、只读权限视图、MCP 工具只读展示和 Unicode 非 emoji 的设计取向，需要一个可由后续实现者直接执行的自包含计划。

2026-06-09 / Codex: 根据用户追加要求收紧命令重构边界：实现时不保留旧 `handle_command()` 函数或兼容 wrapper，调用点、测试和文档统一迁移到 `dispatch_command()`。
