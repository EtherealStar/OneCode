# 重构 CLI 启动路径：移除 LoadingScreen，跳过未信任 MCP 并在主界面提示

> Historical note, 2026-06-13: this Textual-startup plan was completed during the short-lived Textual UI path and has now been superseded by `docs/exec-plans/completed/cli-inline-terminal-ui-refactor-execplan.md`. It remains in `completed/` as implementation history only; current CLI startup uses `ui/cli/terminal/InlineRepl`, not Textual.

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文件遵循仓库根目录 `PLANS.md`。执行者只凭本文件和当前工作树，应能完成端到端实现、验证和后续维护。本计划聚焦 `ui/cli/` 启动路径，取代当前 Textual CLI 重构中“启动时显示 LoadingScreen，并在启动期通过 Textual TrustScreen 处理 MCP trust”的设计。

## Purpose / Big Picture

用户运行 `uv run python -m ui.cli.app` 时，CLI 应稳定进入 Textual 主界面，而不是短暂显示“正在启动 OneCode…”后以退出码 0 闪退。实现后，启动路径不再使用 LoadingScreen，也不再在 Textual 尚未稳定挂载时弹出 MCP trust 模态窗。运行时先在 Textual 之外完成装配；未信任的项目 MCP stdio server 在启动时 fail closed 跳过，不阻塞启动；进入主界面后，用户在消息区看到一条明确提示，说明哪些 MCP server 被跳过以及如何处理。

完成后可用以下方式观察行为：在真实终端中运行 `uv run python -m ui.cli.app`，程序直接进入主界面，顶部显示 `OneCode · <model> · <cwd>`，消息区显示 banner。如果 `.mcp.json` 中存在未信任的 stdio MCP server，主界面中还会出现一条内联提示，例如 `Skipped untrusted MCP server: <name>`；该 server 的工具不会暴露给模型。

## Progress

- [x] (2026-06-13 目前) 确认 VS Code 集成终端可提供真实 TTY：`stdin/stdout/stderr` 可为 `True True True`，终端本身不是问题。
- [x] (2026-06-13 目前) 确认闪退表现为退出码 `0`，不是 provider 配置异常、Python traceback 或 Textual CSS 编译错误。
- [x] (2026-06-13 目前) 阅读 Textual 8.2.7 源码，确认 `push_screen()` 返回 `AwaitMount`，`pop_screen()` 返回 `AwaitComplete`，当前代码未等待这些异步屏幕切换，存在启动竞态。
- [x] (2026-06-13 目前) 与用户确认目标方向：不需要 LoadingScreen；启动时跳过未信任 MCP，进入主界面后提示用户。
- [x] (2026-06-13) 重构 `ui/cli/app.py`：让 TTY 路径在启动 Textual 前同步构建 `CliRuntime`，并把已构建 runtime 传给 `OneCodeApp`。
- [x] (2026-06-13) 重构 `ui/cli/tui/app.py`：让 `OneCodeApp` 不再负责 runtime 装配，不再 push LoadingScreen，不再使用 `_init_runtime_worker()`。
- [x] (2026-06-13) 删除未使用的启动期 UI：`ui/cli/tui/loading_screen.py` 和 `ui/cli/tui/trust.py`，移除相关 import、测试和文档描述。
- [x] (2026-06-13) 为未信任 MCP server 增加主界面内联提示，保持 fail closed，不自动连接、不暴露工具。
- [x] (2026-06-13) 更新 CLI 架构文档和相关测试，验证启动稳定、batch 路径不变、未信任 MCP 提示可见。

## Surprises & Discoveries

- Observation: 用户在 VS Code 终端运行 TTY 检查得到 `True True True`，说明 Textual 可以接管该终端；闪退不是 VS Code 终端不支持 Textual。
  Evidence: 用户提供的输出包含 `True True True`。

- Observation: 闪退后的 `$LASTEXITCODE` 为 `0`，且 `tui-crash.log` 只有 asyncio debug 慢任务提示。
  Evidence: 用户提供的输出包含 `0` 和 `Executing <Task pending ...> took 0.125 seconds`。这类输出来自 `-X dev` 的 asyncio debug，不是异常 traceback。

- Observation: Textual 8.2.7 的 `App.push_screen()` 不是同步挂载。它返回 `AwaitMount`，用于等待 screen 及其子控件挂载完成。`App.pop_screen()` 返回 `AwaitComplete`，用于等待屏幕替换完成。当前 `ui/cli/tui/app.py` 在 `on_mount()`、`_init_runtime_worker()` 中调用这些方法但没有 `await`，随后立即访问 `screen.message_log` 和 `screen.prompt_input`，因此存在屏幕生命周期竞态。
  Evidence: `.venv/Lib/site-packages/textual/app.py` 中 `push_screen()` 返回 `AwaitMount | asyncio.Future`，`pop_screen()` 返回 `AwaitComplete`。当前代码在 `ui/cli/tui/app.py` 的 `on_mount()` 调用 `self.push_screen(LoadingScreen())`，在 runtime 初始化完成后调用 `self.pop_screen()` 和 `self.push_screen(screen)`，均未等待。

- Observation: `pop_screen()` 对最后一个 screen 并不是静默退出，而是在 screen stack 长度小于等于 1 时抛 `ScreenStackError`。因此本计划不把根因简单归结为“弹空栈”，而是归结为“启动阶段 screen push/pop 未等待，且不必要地引入了临时 screen 和后台装配竞态”。
  Evidence: Textual 8.2.7 源码中 `pop_screen()` 在 `len(screen_stack) <= 1` 时 `raise ScreenStackError("Can't pop screen; there must be at least one screen on the stack")`。

## Decision Log

- Decision: 移除 LoadingScreen，而不是修补 LoadingScreen 的 push/pop await 顺序。
  Rationale: 用户明确表示不需要 loading screen。启动界面没有产品价值，却引入了 Textual 生命周期竞态、后台 worker、错误吞吐和启动期 modal 复杂度。删除它比修补它更符合当前需求。
  Date/Author: 2026-06-13 / 用户与执行 agent。

- Decision: `OneCodeApp` 不再负责 `build_runtime()`，改为接收已经构建好的 `CliRuntime`。
  Rationale: Textual app 应负责 UI，不应同时负责 runtime 装配。把 runtime 装配移到 `ui/cli/app.py` 的入口层后，Textual 启动时即可直接挂载 MainScreen，避免“先 loading，后 main”的屏幕切换竞态。启动失败也能在普通终端输出，避免 alternate screen 吞掉错误。
  Date/Author: 2026-06-13 / 执行 agent。

- Decision: 启动时未信任 MCP stdio server 默认跳过，并在主界面提示；不在启动期弹 Textual TrustScreen。
  Rationale: MCP trust 是安全边界，必须 fail closed。未信任 server 不应运行、不应发现工具、不应注入 instructions。但 trust 确认不应阻塞 TUI 启动，也不应在 Textual 尚未稳定挂载时弹 modal。进入主界面后提示用户，既保留安全性，也减少启动路径复杂度。
  Date/Author: 2026-06-13 / 用户与执行 agent。

- Decision: 保留 batch 路径语义：stdin 非 TTY 时仍使用 `ui/cli/batch.py`；Textual 只在 stdin 和 stdout 都是 TTY 时启动。
  Rationale: 非交互管道输入是 CLI 的稳定后备路径。Textual 需要真实输出终端，不能在 stdout 被捕获时可靠运行。
  Date/Author: 2026-06-13 / 执行 agent。

## Outcomes & Retrospective

已实现。TTY 入口现在先在 Textual 外构建 runtime，再启动 `OneCodeApp(runtime)` 并直接挂载 `MainScreen`；启动期 `LoadingScreen`、`TrustScreen` 和 `_init_runtime_worker()` 已删除。未信任项目 stdio MCP server 在 TTY 路径中不再询问 trust，而是记录到 `state.metadata["mcp_untrusted_servers"]`，由 MCP manager fail closed 标记为 `untrusted`，主界面消息区显示 skipped/untrusted 提示。batch 路径仍使用原有 stdin trust prompt 与流式 stdout。

## Context and Orientation

OneCode 是一个 Python code agent runtime。核心 agent 主循环在 `core/loop.py`，工具、权限、MCP、记忆、上下文等能力在 `services/`，CLI 只是 UI 和应用装配层。本文只改 `ui/cli/` 附近，不改变 agent 主循环、工具执行、安全策略或 provider 协议。

当前 CLI 入口在 `ui/cli/app.py`。其中 `main()` 判断是否 TTY：非交互输入走 `ui/cli/batch.py`，TTY 路径启动 Textual app。`build_runtime(workspace, ...)` 负责创建 `CliRuntime`，它聚合运行状态、消息存储、工具注册表、模型客户端、权限策略、MCP manager、附件收集器、trace/error log 等组件。`CliRuntime` 的定义在 `ui/cli/types.py`。

当前 Textual app 在 `ui/cli/tui/app.py` 的 `OneCodeApp`。它的 `on_mount()` 先 `push_screen(LoadingScreen())`，然后启动 `_init_runtime_worker()`。这个 worker 在线程中调用 `build_runtime()`，完成后 `pop_screen()` 移除 loading，再 `push_screen(MainScreen(...))`。`MainScreen` 在 `ui/cli/tui/main_screen.py`，包含 header、message log、streaming preview、status panel 和 prompt input。权限确认由 `ui/cli/tui/permission.py` 的 `TextualPermissionPrompter` 在工具执行期间弹出 `PermissionScreen`。

MCP 是 Model Context Protocol。OneCode 支持从项目配置加载 MCP server，让外部进程向 agent 暴露额外工具。项目 MCP 配置由 `services/mcp` 下的 `load_project_mcp_config()` 读取。stdio MCP server 是本地子进程，可能执行项目配置中的命令，因此必须先被信任。当前 `ui/cli/app.py` 的 `_prompt_for_project_mcp_trust()` 会在 `build_runtime()` 期间询问是否信任。如果传入 Textual trust callback，它会用 `TrustScreen` 在启动期弹 modal；如果没有 callback，它会用普通 stdin/stdout 提示。MCP manager 本身已有 fail-closed 行为：未信任 stdio server 不应连接、不应发现工具、不应注入 instructions。

“TTY” 是真实终端设备。Textual 需要 stdin 接收键盘输入，也需要 stdout 控制全屏绘制、光标和鼠标。如果 stdout 不是 TTY，例如被测试工具、IDE 输出窗口或 Codex shell 工具捕获，Textual 不能可靠运行。

## Plan of Work

第一阶段重构入口装配。修改 `ui/cli/app.py` 的 `main()`，让 TTY 路径先在普通终端环境中调用 `build_runtime()`，再创建 `OneCodeApp(runtime)` 并运行。此时 `OneCodeApp` 构造函数不再接收 `workspace: Path`，而是接收 `runtime: CliRuntime`。启动失败时仍由 `main()` 捕获 `ProviderError` 和普通异常并输出错误。这样 Textual 启动前 runtime 已经可用，Textual 不需要 loading worker。

第二阶段重构权限 prompter 绑定。`build_runtime()` 需要一个 `PermissionPrompter`，但 Textual 权限 prompter 需要 app 实例才能弹 modal。为解决这个先后依赖，引入一个小型可绑定 prompter，例如放在 `ui/cli/tui/permission.py` 中。它实现 `services.permissions.PermissionPrompter` 协议，构造时没有 app；`OneCodeApp` 创建后调用 `bind(app)`。在绑定前如果收到权限请求，应保守返回 deny 或抛出清晰内部错误；正常路径中权限请求只会发生在用户提交 prompt 后，此时 app 已绑定。这样 `build_runtime()` 可以在 Textual app 创建前完成，同时工具执行期间仍使用 Textual permission modal。

第三阶段删除 LoadingScreen 启动路径。修改 `ui/cli/tui/app.py`：删除 `_init_runtime_worker()`，删除 `_trust_prompt()` 中的 `threading.Event` 和 `call_from_thread` 桥接，删除 `LoadingScreen` import。`OneCodeApp.on_mount()` 只负责创建并挂载 `MainScreen`，写入 banner，focus prompt input。由于 `push_screen()` 返回 awaitable，`on_mount()` 应等待 MainScreen 挂载完成后再访问 `message_log` 和 `prompt_input`。如果采用 Textual 推荐的 `await self.push_screen(screen)`，就不要在 await 前访问子控件。

第四阶段调整 MCP trust 策略。`build_runtime()` 不应在 TTY Textual 路径阻塞式询问 trust，也不应调用 Textual `TrustScreen`。实现时可以保留 `_prompt_for_project_mcp_trust()` 供非交互或未来命令使用，但 TTY 主路径应使用“skip untrusted”策略：扫描项目 MCP 配置，找出 enabled 的 stdio server；如果 trust store 中没有对应 fingerprint，则不写 trust、不连接，让 `McpConnectionManager` 的 trust policy 保持 fail closed。同时把未信任 server 的摘要记录到 `RuntimeState.metadata`，例如 `state.metadata["mcp_untrusted_servers"]`，每个元素包含 server name、command、args、cwd、explicit env keys 和 base env keys。字段应使用普通 dict/list，便于 CLI 渲染和 transcript/debug 输出。

第五阶段在主界面提示用户。新增或复用一个小的 Rich renderable，用于把 `state.metadata["mcp_untrusted_servers"]` 渲染为内联提示。最简单位置是在 `OneCodeApp.on_mount()` 写完 banner 后，如果存在 untrusted server metadata，就调用 `screen.message_log.write(...)`。提示内容应短而明确：哪些 server 被跳过；因为未信任，所以没有运行；它们的工具未暴露。不要在本计划中实现 `/mcp trust`，除非已有命令体系天然支持；本计划只要求启动不阻塞且用户可见。若未来需要交互式 trust，可另开 ExecPlan。

第六阶段删除未使用文件和更新文档。删除 `ui/cli/tui/loading_screen.py` 和 `ui/cli/tui/trust.py`，移除 `theme.tcss` 中 `LoadingScreen` 和 `TrustScreen` 的样式，移除相关 import。更新 `docs/design-docs/cli-architecture.md`：入口分流改为“TTY 先构建 runtime，再启动 MainScreen”；模态交互列表中删除启动期 `TrustScreen`；数据流图中删除 LoadingScreen。保留 `PermissionScreen`、`PageScreen`、`SelectScreen`、`ConnectScreen`。

第七阶段补测试。用 `App.run_test()` 或直接实例化 app 验证 `OneCodeApp` 能在已有 fake runtime 下挂载 MainScreen，不需要 LoadingScreen。新增测试验证：存在 `state.metadata["mcp_untrusted_servers"]` 时，主界面 message log 包含 skipped/untrusted 提示。更新入口测试验证：stdin/stdout 都是 TTY 时，`main()` 会构建 runtime 并启动 `OneCodeApp(runtime)`；stdin 非 TTY 时仍走 batch；stdout 非 TTY 时输出清晰错误并返回 1。更新 MCP trust 相关测试，确认 TTY 启动路径不调用 startup TrustScreen，未信任 server 被记录并跳过。

## Concrete Steps

所有命令在仓库根目录 `D:\study\OneCode` 执行。

先确认当前问题可复现。在真实终端运行：

    uv run python -c "import sys; print(sys.stdin.isatty(), sys.stdout.isatty(), sys.stderr.isatty())"
    uv run python -X dev -m ui.cli.app 2> tui-crash.log
    echo $LASTEXITCODE
    Get-Content .\tui-crash.log

问题场景中第一条命令应输出 `True True True`，第二条命令短暂显示“正在启动 OneCode…”后退出，`$LASTEXITCODE` 为 `0`，日志只包含 asyncio debug 慢任务提示或没有 traceback。

然后实施重构。先改 `ui/cli/tui/permission.py`，加入可绑定 Textual permission prompter；再改 `ui/cli/app.py` 的 TTY 入口，让它构建 runtime 后创建 `OneCodeApp(runtime)`；再改 `ui/cli/tui/app.py`，删除 loading worker 和 trust callback，`on_mount()` 只挂 MainScreen；再处理 MCP untrusted metadata 和主界面提示；最后删除 dead files 和 dead styles。

每完成一个阶段，运行聚焦测试：

    uv run python -m pytest tests\test_async_cli_streaming.py tests\test_cli_tui.py tests\test_cli_mcp_trust_prompt.py -q

实现完成后运行更广测试：

    uv run python -m pytest tests -q
    uv run python -m compileall ui\cli

最后在真实终端手动验证：

    uv run python -m ui.cli.app

预期不再出现 LoadingScreen。程序应直接进入主界面。按 `/status` 回车应打开状态页；按 Esc 返回。输入 `/exit` 应退出，并保持 `$LASTEXITCODE` 为 `0`。

## Validation and Acceptance

本计划的核心验收是用户可观察行为。

在没有未信任 MCP server 的工作区中，运行 `uv run python -m ui.cli.app` 后，应直接进入主界面，不显示“正在启动 OneCode…”。主界面应包含 banner、Header、MessageLog、StreamingPreview、StatusPanel 和 PromptInput。输入 `/status` 应显示状态页；输入 `/exit` 应正常退出。

在存在未信任 stdio MCP server 的工作区中，运行同一命令后，仍应直接进入主界面，不出现启动期 trust modal。消息区应出现一条提示，说明某个 MCP server 因未信任被跳过。该 server 不应连接，其工具不应出现在 `/mcp` 状态或模型可见工具列表中。执行者可以通过 `/mcp` 或相关状态视图确认 server 状态为 untrusted/skipped，具体显示以现有 MCP view 为准。

非交互 batch 路径保持不变。运行：

    "hello" | uv run python -m ui.cli.app

应走 `ui/cli/batch.py`，不启动 Textual，不显示全屏控制序列。若 provider 配置可用，应输出模型回答；若 provider 配置不可用，应输出现有错误格式。

测试验收包括：

    uv run python -m pytest tests\test_async_cli_streaming.py tests\test_cli_tui.py tests\test_cli_mcp_trust_prompt.py -q
    uv run python -m compileall ui\cli
    uv run python -m pytest tests -q

新增或更新的测试应在旧启动路径下失败，在重构后通过。尤其需要覆盖 `OneCodeApp` 不依赖 LoadingScreen、`main()` 的 TTY 分流、未信任 MCP metadata 提示和 batch 路径不变。

## Idempotence and Recovery

本计划的修改是可重复的。删除 LoadingScreen 和 TrustScreen 前，先确保没有 import 残留，可用：

    rg -n "LoadingScreen|TrustScreen|_init_runtime_worker|_trust_prompt" ui\cli tests docs

如果实现中间失败，可以临时保留文件但移除入口引用；只要测试通过，后续再删除 dead files。不要使用 `git reset --hard` 或回退用户未提交改动。当前工作树已有大量 Textual CLI 重构改动，执行者必须只修改本计划相关文件，避免回滚无关变更。

如果未信任 MCP 提示实现有问题，安全 fallback 必须是跳过 server，而不是自动信任或自动运行。任何 trust UX 不完整都不能导致 stdio MCP server 在未信任状态下启动。

如果 Textual app 仍闪退，优先用以下命令收集证据：

    uv run python -X dev -m ui.cli.app 2> tui-crash.log
    echo $LASTEXITCODE
    Get-Content .\tui-crash.log

若退出码仍为 `0` 且无 traceback，继续检查 `OneCodeApp.on_mount()` 是否还有未等待的 screen 切换，或是否调用了 `self.exit()`。若退出码非 0，按 traceback 定位。

## Artifacts and Notes

用户已提供的关键排查输出：

    True True True
    0
    Executing <Task pending name='Task-1' coro=<App.run.<locals>.run_app() running at D:\study\OneCode\.venv\Lib\site-packages\textual\app.py:2336> ...> took 0.125 seconds

这说明真实终端可用，程序正常退出，不是崩溃。`Executing <Task pending ...> took 0.125 seconds` 是 asyncio debug 提示，不能当作异常根因。

Textual 8.2.7 源码中的相关事实：

    def push_screen(...) -> AwaitMount | asyncio.Future:
        ...
        return await_mount

    def pop_screen(self) -> AwaitComplete:
        if len(screen_stack) <= 1:
            raise ScreenStackError(...)
        ...
        return AwaitComplete(do_pop()).call_next(self)

当前 OneCode 启动路径中的危险模式：

    async def on_mount(self) -> None:
        self.push_screen(LoadingScreen())
        self._init_runtime_worker()

    ...
    self.runtime = runtime
    self.pop_screen()
    screen = MainScreen(...)
    self.push_screen(screen)
    screen.message_log.write(...)
    screen.prompt_input.focus()

计划完成后，这类启动期 push/pop 链应不存在。Textual app 应从已有 runtime 直接挂载 MainScreen。

## Interfaces and Dependencies

本计划不新增第三方依赖。继续使用 `textual>=8.2.7`、Rich 和现有 OneCode services。

`ui/cli/app.py` 最终应继续提供：

    def build_runtime(
        workspace: Path,
        *,
        trust_prompt: Callable[[McpTrustPromptRequest], TrustChoice] | None = None,
        permission_prompter: PermissionPrompter | None = None,
    ) -> CliRuntime

实现可以保留 `trust_prompt` 参数以兼容测试或未来 fallback，但 TTY Textual 主路径不应再传入 Textual trust callback。若保留 `_prompt_for_project_mcp_trust()`，它不应在 Textual startup path 中被调用。更好的长期形态是把 trust 预处理拆成两个清晰函数：一个用于普通终端显式询问，一个用于启动时收集并跳过 untrusted server。

`ui/cli/tui/app.py` 最终应提供：

    class OneCodeApp(App):
        def __init__(self, runtime: CliRuntime) -> None: ...
        async def on_mount(self) -> None: ...

`OneCodeApp` 不应 import `build_runtime`，不应 import `LoadingScreen`，不应启动 `_init_runtime_worker()`。它仍负责 `run_agent()`、slash command dispatch、PageScreen、SelectScreen、ConnectScreen、PermissionScreen 和 shutdown flush。

`ui/cli/tui/permission.py` 应提供一个可以在 runtime 构建前传入、app 创建后绑定的 permission prompter。名称可由实现者选择，例如：

    class DeferredTextualPermissionPrompter:
        def bind(self, app: OneCodeApp) -> None: ...
        async def request_permission(self, request: PermissionRequest) -> PermissionResponse: ...

它必须实现与 `services.permissions.PermissionPrompter` 相同的行为。绑定后，它应复用现有 `PermissionScreen`。未绑定时不得允许危险操作；正常路径不应在未绑定状态收到权限请求。

`RuntimeState.metadata` 中建议新增键：

    "mcp_untrusted_servers": tuple[dict[str, str], ...]

每个 dict 至少包含：

    name
    command
    args
    cwd
    explicit_env_keys
    base_env_keys

主界面渲染只消费这些字段，不重新读取 `.mcp.json`，不重新计算 fingerprint，不执行任何 MCP 命令。

修订说明：2026-06-13 创建本 ExecPlan，原因是用户确认不需要 LoadingScreen，并选择“启动时跳过 untrusted MCP，进入主界面后提示用户”的方向。本计划把启动闪退排查结论、Textual push/pop 语义、MCP fail-closed 目标和重构步骤合并为一份可执行文档。
