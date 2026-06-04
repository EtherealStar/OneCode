# 实现 CLI 交互主界面

本 ExecPlan 是一个活文档。实现过程中必须持续维护 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective`。本计划遵守仓库根目录的 `PLANS.md`，并把必要背景写入本文，使后续执行者只阅读本文和当前工作区也能完成实现。

## Purpose / Big Picture

完成本改动后，用户可以在仓库根目录运行 `uv run python -m ui.cli.app` 启动 OneCode 的第一个 CLI 交互主界面。界面提供单行 prompt 输入、固定的 `read_file` 和 `edit_file` 工具、当前会话状态、工具列表、历史消息查看、从 JSONL transcript 恢复会话、清空当前会话和退出能力。它不是完整终端 UI 框架，不引入新依赖，不实现美化、流式渲染、权限交互或离线测试模式。

用户能通过真实 `.env` 模型配置看到它工作：启动 CLI，输入一个普通任务，CLI 调用现有 `AgentLoop`，模型可以使用已注册文件工具，最终 assistant 文本打印回终端。用户也能通过 `/resume <path-or-session-id>` 读取 `.onecode/<session_id>/messages.jsonl`，再用 `/history` 查看恢复出的消息摘要，并在同一 session 上继续对话。

## Progress

- [x] (2026-06-04 11:05Z) 讨论并确认产品边界：按轻量标准库 CLI 实现；只支持单行输入；支持 JSONL 恢复和历史查看；不做权限交互；工具固定为 `read_file` 和 `edit_file`；不做测试/离线模式。
- [x] (2026-06-04) 新增 `ui/cli/` 模块，提供 CLI 应用入口、命令处理和文本渲染。
- [x] (2026-06-04) 实现 runtime 装配：`RuntimeState`、`MessageStore`、`ContextEngine`、provider model client、固定工具 registry、sandbox guard 和 registry executor。
- [x] (2026-06-04) 实现内置 slash commands：`/help`、`/tools`、`/status`、`/history`、`/resume`、`/clear`、`/exit`。
- [x] (2026-06-04) 为命令处理和渲染补充 focused tests，不增加用户可见的 dry-run 或离线模式。
- [x] (2026-06-04) 更新 `architecture.md` 和相关技术债记录，说明 `ui/cli/` 第一版已经落地但仍缺 streaming、observability 和权限交互。
- [x] (2026-06-04) 运行 compile check 和测试：`uv run python -m compileall core services infrastructure tools ui`、`uv run python -m pytest tests -q` 均通过。
- [x] (2026-06-04) 运行非交互启动烟测；当前 `.env` 的 `custom` provider 缺少 base URL，CLI 输出清晰配置错误并退出，没有 Python traceback。

## Surprises & Discoveries

- Observation: 当前 `ui/` 目录尚未实现，但 `architecture.md` 已把 CLI 明确放在 `ui/cli/app.py`、`ui/cli/commands.py` 和 `ui/cli/renderer.py`。
  Evidence: `architecture.md` 的目标目录结构和 UI 边界章节写明 CLI 是 UI 的一种实现，不应直接实现 agent 逻辑。

- Observation: 当前项目依赖只有 `python-dotenv`，没有 Rich、Textual 或 prompt-toolkit。
  Evidence: `pyproject.toml` 的 runtime dependencies 只有 `python-dotenv>=1.0.1`。第一版 CLI 应使用标准库 `input()` 和 `print()`，避免为了简单交互引入 UI 框架。

- Observation: JSONL 恢复所需服务已经存在。
  Evidence: `services/context/message_store.py` 提供 `MessageStore.from_transcript(transcript_store, state)`，`services/context/transcript.py` 提供 `JsonlTranscriptStore.load_messages()`，恢复时会把 transcript 中的 session id 写回 `RuntimeState.session_id`。

- Observation: 当前主循环没有 streaming 或可观测事件订阅接口。
  Evidence: `core/loop.py` 的 `AgentLoop.run(prompt)` 同步返回最终文本，中间工具调用只进入 `MessageStore.append_tool_results()`；`services/observability/` 仍是目标模块。CLI 第一版不能承诺实时 token 或工具进度渲染。

- Observation: Windows 绝对路径不能交给默认 `shlex.split()` 解析。
  Evidence: `/resume C:\...` 中的反斜杠会被 POSIX 风格 `shlex` 当作转义符，导致 transcript path 解析失败。命令处理改为把 `/resume` 后的整段文本作为目标路径，并只剥离成对引号。

## Decision Log

- Decision: 启动入口使用 `uv run python -m ui.cli.app`，不先添加 console script。
  Rationale: 用户确认按建议实现。模块入口能直接复用当前 Python 包结构，避免在第一版里修改打包配置。


- Decision: 第一版只支持单行输入。
  Rationale: 用户明确要求只做单行。多行粘贴、编辑器模式和输入历史搜索都属于后续交互增强。


- Decision: JSONL 功能提供 `/resume <path-or-session-id>` 和 `/history [n]`。
  Rationale: 用户要求支持从 JSONL 读取内容。`/resume` 把现有 transcript 恢复为当前会话，`/history` 展示当前内存消息摘要；这样既能查看恢复内容，也能继续写入同一个 session。


- Decision: 第一版不做权限交互。
  Rationale: 用户明确不要权限交互。当前 guard 的 ask/deny 会通过工具错误返回给模型或显示为最终结果；未来结构化权限机制落地后再加用户确认 UI。


- Decision: 工具注册范围固定为 `read_file` 和 `edit_file`。
  Rationale: 用户明确要求先固定范围。两者已经有 descriptor、guard 集成和测试，适合作为 CLI 主界面的初始工具集。


- Decision: 不提供用户可见的测试/离线模式。
  Rationale: 用户明确指出 dry-run 或 echo 模式是过度设计。测试可以使用 fake 对象验证命令和渲染，但 CLI 产品功能不包含离线模拟运行。


- Decision: 将 `CliRuntime` 和 `CommandResult` 放到 `ui/cli/types.py`。
  Rationale: `app.py` 需要导入 `commands.py`，`commands.py` 又需要返回可替换 runtime。中立类型模块避免循环导入，并让 `/resume` 能重建绑定到恢复后 `MessageStore` 的 `AgentLoop`。


## Outcomes & Retrospective

第一版 CLI 已实现。用户可以运行 `uv run python -m ui.cli.app` 启动标准库 REPL，查看固定文件工具、状态和历史，输入普通 prompt 调用真实 `AgentLoop.run()`，用 `/clear` 开新 session，并用 `/resume <session-id-or-jsonl>` 从 JSONL transcript 恢复会话后继续对话。

仍留给后续计划的能力包括 streaming token、结构化 observability 事件、权限 ask 交互、`/compact`、provider connect/model selection flow，以及 provider/context/max-output recovery 的用户友好展示。本计划没有引入 Rich/Textual/prompt-toolkit，也没有新增用户可见 dry-run 或离线模式。

## Context and Orientation

OneCode 是 Python code agent runtime。`core/loop.py` 中的 `AgentLoop` 是主循环，它接收用户 prompt，把用户消息写入 `MessageStore`，通过 `ContextEngine` 构建模型上下文，调用注入的 `ModelClient`，再执行模型请求的工具调用。主循环不应该知道 CLI 命令、具体工具注册列表、provider 配置文件格式或终端渲染细节。

`core/context_engine.py` 是每轮模型调用前重建上下文的边界。它从 `MessageStore.current_messages()` 取当前消息，调用 prompt assembler 生成 system prompt，调用 tool schema provider 生成模型可见工具 schema。第一版 CLI 可以使用 `StaticPromptAssembler` 的默认空 prompt，也可以传入很短的静态 system prompt，但不得把工具规则或权限逻辑硬编码进 CLI。

`services/context/message_store.py` 是会话消息状态来源。它把消息保存在内存中供模型上下文读取，同时写入 `.onecode/<session_id>/messages.jsonl`。`MessageStore.clear_for_new_session(new_session_id)` 能清空内存并切换 transcript 写入目录。`MessageStore.from_transcript(transcript_store, state)` 能从既有 JSONL 恢复内存消息，并把 `state.session_id` 替换为 transcript 中的 session id。

`services/context/transcript.py` 的 `JsonlTranscriptStore` 负责 JSONL 文件路径、读取和写入。它的 `root_dir` 通常是项目根目录下的 `.onecode`。如果用户给 `/resume` 的参数是 session id，CLI 应解析为 `.onecode/<session_id>/messages.jsonl`；如果参数是文件路径，CLI 应接受指向 `messages.jsonl` 的路径，并从其父目录推出 session id 和 transcript root。

`infrastructure/providers/factory.py` 提供 `create_model_client(".env")`，它只从项目根目录 `.env` 读取 provider 配置。CLI 应直接使用这个入口，让缺失或错误配置以清晰错误显示给用户。不要在 CLI 中读取系统环境变量替代 `.env`，也不要把 provider-specific 字段泄露到 `core/loop.py`。

`services/tools/registry.py` 的 `ToolRegistry` 管理工具 descriptor。第一版 CLI 应固定注册 `tools.read_file.descriptor()` 和 `tools.edit_file.descriptor()`。`RegistryToolExecutor` 位于 `services/tools/executor.py`，执行工具前会校验 schema、工具输入、input-aware classification 和 sandbox guard。CLI 只负责装配 executor，不直接执行工具 handler。

`services/guard/` 提供 sandbox 边界。CLI 启动时应以当前工作目录作为 workspace，创建 `SandboxBoundary(cwd=workspace)` 和 `SandboxGuard(boundary)`，再注入 `RegistryToolExecutor`。这样文件工具默认只能在项目边界内工作，外部路径 ask/deny 仍由已有 guard 逻辑处理。

参考 UI 文件 `docs/references/ui显示/REPL.tsx` 展示了成熟终端 REPL 的消息区、输入区、命令、状态、权限提示和 transcript 视图。但 OneCode 第一版不复制 React/Ink 架构，只借鉴概念：一个主输入循环、几个 slash commands、状态文本、消息历史查看和运行中提示。

## Plan of Work

第一阶段创建 UI 目录。新增 `ui/__init__.py`、`ui/cli/__init__.py`、`ui/cli/renderer.py`、`ui/cli/commands.py` 和 `ui/cli/app.py`。`renderer.py` 只做文本格式化和 `print()` 输出，不持有 runtime；`commands.py` 只解析 slash command 并调用传入的应用状态对象；`app.py` 负责启动、装配 runtime、循环读取输入和调度普通 prompt 或 slash command。

第二阶段定义 CLI 应用状态。可以在 `ui/cli/app.py` 中定义小型 dataclass，例如 `CliRuntime`，字段包括 `workspace: Path`、`state: RuntimeState`、`message_store: MessageStore`、`registry: ToolRegistry`、`loop: AgentLoop` 和 `model_config_summary`。装配函数命名为 `build_runtime(workspace: Path) -> CliRuntime`。它读取 `.env` 创建模型客户端，创建 `RuntimeState`，创建指向 `workspace / ".onecode"` 的 `MessageStore`，注册固定工具，创建 `ContextEngine(message_store, tool_schema_provider=registry)`，创建 `SandboxGuard(SandboxBoundary(cwd=workspace))`，最后创建 `AgentLoop`。

第三阶段实现主循环。`main()` 使用 `Path.cwd()` 作为 workspace，调用 `build_runtime()`，打印启动 banner，然后进入 `while True`。每轮使用 `input("onecode> ")` 读取单行。空输入跳过。以 `/` 开头的输入交给 `commands.py`；其他输入调用 `renderer.render_running()`，再调用 `runtime.loop.run(user_input)`，最后用 `renderer.render_assistant(final_text)` 打印结果。捕获 `KeyboardInterrupt` 时打印一行提示并继续或退出，捕获 `EOFError` 时 flush transcript 并退出。退出前必须调用 `message_store.flush_transcript()`。

第四阶段实现 slash commands。`/help` 显示命令列表。`/tools` 遍历 `runtime.registry.descriptors()`，输出工具 name 和 description。`/status` 输出 workspace、session id、provider display name、model、turn count、last transition 和 usage token 统计。`/history [n]` 从 `runtime.message_store.current_messages()` 读取最近 N 条内部消息，默认 N 为 20，只打印 role、工具名或 tool call id、错误标记和 content 预览，不打印完整大内容。`/clear` 调用 `new_session_id = runtime.state.start_new_session()`，再调用 `runtime.message_store.clear_for_new_session(new_session_id)`，并提示旧 session 已保留在 `.onecode/`。`/resume <path-or-session-id>` 解析 JSONL，创建 `JsonlTranscriptStore`，调用 `MessageStore.from_transcript()`，并重建依赖该 message store 的 `ContextEngine` 和 `AgentLoop`，保留同一个 provider client、registry 和 executor 或通过装配 helper 重新创建 loop。`/exit` 和 `/quit` flush transcript 后退出。

第五阶段实现 `/resume` 路径解析。若参数以 `.jsonl` 结尾或解析为存在的文件，则必须指向 `messages.jsonl` 或至少是一个 JSONL 文件；用 `session_dir = path.parent`，`root_dir = session_dir.parent`，`session_id = session_dir.name` 创建 `JsonlTranscriptStore(root_dir, session_id, cwd=workspace)`。若参数不是文件路径，则按 session id 解析为 `workspace / ".onecode" / session_id / "messages.jsonl"`。文件不存在时返回用户可读错误，不改变当前 runtime。恢复成功后，`state.turn_count` 可重置为 0，usage 可保留当前状态或重置；推荐调用一个小 helper 重新创建 `RuntimeState(session_id=loaded_session_id, max_turns=old_state.max_turns)`，再用 `MessageStore.from_transcript()` 覆盖 session id，避免旧对话的 token 统计误导 `/status`。

第六阶段补充测试。新增 `tests/test_cli_commands.py`，使用真实 `RuntimeState`、临时 `MessageStore` 和 fake runtime 对象测试 `/help`、`/tools`、`/status`、`/history`、`/clear` 和未知命令。新增 `tests/test_cli_resume.py`，用 `tmp_path` 创建 `MessageStore` 写入 user/assistant/tool_result 后 flush，再通过 CLI 的解析/恢复 helper 恢复，断言 `current_messages()` 包含原消息且 session id 使用 transcript session。不要新增 CLI 的 dry-run、echo 或离线产品模式；测试中的 fake loop 只用于不触发真实 provider。

第七阶段更新文档。编辑 `architecture.md`，把 `ui/cli/` 从目标尚未实现改为第一版已落地，并说明它只做应用装配、单行输入、slash commands 和文本渲染。更新 `docs/tech-debt/tech-debt-tracker.md`，如有必要新增或调整 UI 相关技术债：CLI 已有主界面，但仍缺结构化 observability、streaming 渲染、权限交互和 provider/recovery 友好错误路径。不要把这些后续能力塞进第一版实现。

## Concrete Steps

在仓库根目录执行所有命令：

    cd D:\study\OneCode

开始前查看工作区，确认不要覆盖用户已有改动：

    git status --short

按以下顺序编辑：

1. 新建 `ui/__init__.py` 和 `ui/cli/__init__.py`。
2. 新建 `ui/cli/renderer.py`，实现 banner、assistant、error、status、tools 和 history 的纯文本渲染函数。
3. 新建 `ui/cli/commands.py`，实现 slash command 解析和处理函数。命令处理应返回一个结构化结果，例如 `CommandResult(should_exit: bool = False, runtime_replaced: CliRuntime | None = None)`，避免用异常控制普通命令流。
4. 新建 `ui/cli/app.py`，实现 `build_runtime()`, `main_loop()` 和 `main()`，并添加 `if __name__ == "__main__": main()`。
5. 新增 focused tests，优先覆盖不需要真实 provider 的命令和恢复 helper。
6. 更新 `architecture.md` 和 `docs/tech-debt/tech-debt-tracker.md`。

实现后先运行 focused tests：

    uv run python -m pytest tests/test_cli_commands.py tests/test_cli_resume.py -q

再运行编译检查：

    uv run python -m compileall core services infrastructure tools ui

最后运行全量测试：

    uv run python -m pytest tests -q

手动验证需要项目根目录 `.env` 已配置可用 provider：

    uv run python -m ui.cli.app

预期启动后显示 workspace、session id、provider/model 和命令提示。输入 `/tools` 应显示 `edit_file` 和 `read_file`。输入 `/status` 应显示当前 session 和 usage。输入 `/history` 应显示当前消息摘要。输入普通 prompt 后，应返回 assistant 文本。输入 `/clear` 后 session id 应变化，旧 `.onecode/<old_session_id>/messages.jsonl` 保留。输入 `/resume <old_session_id>` 后，`/history` 应能看到旧 session 消息摘要。

## Validation and Acceptance

验收标准一：运行 `uv run python -m ui.cli.app` 能启动 CLI，不需要额外依赖。启动失败时，如果 `.env` 缺失或 provider 配置错误，CLI 用一段清晰错误文本说明问题，并退出或返回 shell，不打印 Python traceback 作为主要用户体验。

验收标准二：输入 `/tools` 只显示两个固定工具：`read_file` 和 `edit_file`。模型可见工具 schema 也来自同一个 `ToolRegistry`，不得在 CLI 中另写一份工具列表给模型。

验收标准三：输入普通 prompt 会调用真实 `AgentLoop.run(prompt)`。如果模型请求读取或编辑 workspace 内文件，工具通过 `RegistryToolExecutor` 和 `SandboxGuard` 执行；CLI 不直接调用任何工具 handler。

验收标准四：输入 `/history` 会显示当前内存消息摘要。摘要中 user、assistant 和 tool_result 的 role 可区分，工具结果错误状态可见，长内容会被预览截断，避免把大 JSONL 工具结果完整刷满终端。

验收标准五：输入 `/clear` 会生成新的 `RuntimeState.session_id`，清空当前内存消息，并把后续 transcript 写入 `.onecode/<new_session_id>/messages.jsonl`。旧 session 目录仍保留。

验收标准六：输入 `/resume <session_id>` 或 `/resume <path-to-messages.jsonl>` 会从 JSONL 恢复消息。恢复后 `/status` 显示恢复出的 session id，`/history` 显示恢复出的消息，继续输入普通 prompt 会追加到同一个 session transcript。

验收标准七：输入 `/exit` 或按 EOF 退出前会调用 `MessageStore.flush_transcript()`，确保缓冲的 JSONL 记录落盘。

验收标准八：以下命令通过：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

## Idempotence and Recovery

CLI 启动只读取 `.env` 和创建当前 session 的 `.onecode/<session_id>/` transcript 目录，不修改项目源代码。`/clear` 不删除旧 session。`/resume` 在目标文件不存在、JSONL 为空或解析失败时，不应破坏当前 runtime；它应显示错误并保留原会话。

测试必须使用 `tmp_path` 隔离 transcript root，不写真实项目 `.onecode/`。测试可以构造 fake runtime 或 fake loop 验证命令行为，但这只是测试技术，不是用户可见功能。不要新增 `--dry-run`、`--echo`、`--offline` 等产品入口。

如果 `AgentLoop.run()` 抛出 provider error 或其他未恢复异常，第一版 CLI 可以捕获异常、显示简短错误并回到输入循环。不要在 CLI 中实现 provider retry、context compact 或 max-output recovery；这些属于 runtime transition 和后续错误恢复计划。

## Artifacts and Notes

启动界面示例：

    OneCode CLI
    cwd: D:\study\OneCode
    session: 39093bfa-58de-4ad4-8ec6-893b65785d2e
    model: OpenAI / gpt-4.1
    commands: /help /tools /status /history /resume /clear /exit

    onecode>

`/history` 示例：

    Recent messages:
    [1] user: inspect architecture.md
    [2] assistant: <tool call: read_file>
    [3] tool_result read_file call_read: 1    # OneCode 架构...
    [4] assistant: architecture.md describes...

`/resume` 示例：

    onecode> /resume 39093bfa-58de-4ad4-8ec6-893b65785d2e
    Restored session 39093bfa-58de-4ad4-8ec6-893b65785d2e from .onecode\39093bfa-58de-4ad4-8ec6-893b65785d2e\messages.jsonl.

## Interfaces and Dependencies

`ui/cli/app.py` should expose:

    @dataclass
    class CliRuntime:
        workspace: Path
        state: RuntimeState
        message_store: MessageStore
        registry: ToolRegistry
        loop: AgentLoop
        provider_label: str
        model: str

    def build_runtime(workspace: Path) -> CliRuntime:
        ...

    def main(argv: Sequence[str] | None = None) -> int:
        ...

`ui/cli/commands.py` should expose:

    @dataclass
    class CommandResult:
        should_exit: bool = False
        runtime: CliRuntime | None = None

    def handle_command(runtime: CliRuntime, line: str) -> CommandResult:
        ...

If importing `CliRuntime` from `app.py` creates a cycle, define a small `Protocol` in `commands.py` for the fields commands need, or move `CliRuntime` into a neutral `ui/cli/types.py`. Choose the smallest option that keeps imports clean.

`ui/cli/renderer.py` should expose pure functions that accept plain values and return strings or print directly. Prefer returning strings in renderer functions so tests can assert output without intercepting stdout. `app.py` can call `print(renderer.render_status(...))`.

No new runtime dependency is required. Do not add Rich, Textual, prompt-toolkit or curses in this plan. Standard library functionality is enough for the accepted first version.

2026-06-04 / Codex: 初始计划创建，纳入用户确认的 CLI 范围、单行输入、JSONL 恢复/查看、无权限交互、固定工具和不提供测试/离线模式的决策。
