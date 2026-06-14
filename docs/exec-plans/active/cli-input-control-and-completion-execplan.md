# 改进 CLI 输入控制与补全体验

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文档遵循仓库根目录下的 `PLANS.md`。任何实现或修订本计划的人都必须保持它自包含，并在决策和结果变化时同步更新所有 living sections。

## Purpose / Big Picture（目的与整体图景）

完成此变更后，OneCode 的交互式 CLI 会更像一个成熟的 code-agent 终端界面。启动时，输入框不会再出现类似 `]11;rgb:f8f8/f8f8/f8f8\` 的终端颜色探测回复。agent 回合正在运行时，`Ctrl+C` 会继续取消当前运行回合。没有 agent 回合运行时，第一次按 `Ctrl+C` 会清空当前输入并显示 `Press Ctrl-C again to exit`；1.5 秒内第二次按 `Ctrl+C` 会 flush 状态并退出。Slash 命令补全会以明确的命令列表显示在输入行下方，列表中展示命令和描述，`Tab` 填入高亮命令，`Enter` 接受并提交高亮命令。

这个行为可以通过在 `D:\study\OneCode` 运行 `uv run python -m ui.cli.app` 来观察：输入 `/` 或 `/r`，按 `Tab` 和 `Enter`，并在空闲状态和运行状态分别按一次或两次 `Ctrl+C`。

## Progress（进度）

- [x] (2026-06-14 00:00+08:00) 已研究当前 CLI 输入实现，并识别相关模块：`ui/cli/terminal/detect.py`、`ui/cli/terminal/prompt_session.py`、`ui/cli/terminal/completer.py`、`ui/cli/suggestions.py`、`ui/cli/commands.py`、`ui/cli/terminal/repl.py` 和 `ui/cli/terminal/stream_session.py`。
- [x] (2026-06-14 00:00+08:00) 已确认终端泄漏由 OSC 11 背景色探测路径导致，且泄漏出来的可见文本确实匹配 OSC 11 查询的终端回复。
- [x] (2026-06-14 00:00+08:00) 已确认用户期望的空闲 `Ctrl+C` 行为：如果输入框里有文本，第一次 `Ctrl+C` 清空文本并显示 `Press Ctrl-C again to exit`；1.5 秒内第二次 `Ctrl+C` 退出。
- [x] (2026-06-14 00:00+08:00) 已在 `docs/exec-plans/active/cli-input-control-and-completion-execplan.md` 撰写本 ExecPlan。
- [x] (2026-06-14 00:00+08:00) 实现健壮的终端亮度检测，Windows 上跳过会泄漏的 OSC 11 查询并回退到 `COLORFGBG` / dark fallback。
- [x] (2026-06-14 00:00+08:00) 实现空闲状态双击 `Ctrl+C` 退出语义，同时保留运行回合中的取消行为。
- [x] (2026-06-14 00:00+08:00) 用自定义 slash 命令建议面板替换默认 prompt_toolkit 补全菜单，使其符合目标命令列表体验。
- [x] (2026-06-14 00:00+08:00) 为终端探测 fallback、OSC 11 parser、空闲 `Ctrl+C`、运行中 `Ctrl+C`、命令补全接受和命令过滤行为添加聚焦测试。
- [ ] 运行聚焦 CLI 测试和 compile 检查，然后手动验证交互式 CLI 行为。自动化测试和 compile 已通过；真实 TTY 手动验收仍待执行。
- [ ] 实现并验证完成后，将本 ExecPlan 移动到 `docs/exec-plans/completed/`。

## Surprises & Discoveries（意外发现）

- Observation: 可见字符串 `]11;rgb:f8f8/f8f8/f8f8\` 不是普通输入文本，而是 OSC 11 终端背景色回复的 payload。OSC 是 “Operating System Command”，是一类用于终端宿主查询和设置的终端转义序列。OSC 11 专门询问终端背景色。
  Evidence: `ui/cli/terminal/detect.py` 定义了 `_OSC11_REQUEST = b"\x1b]11;?\x07"`，并解析匹配 `\x1b]11;rgb:...` 的 `_OSC11_REPLY`。`ui/cli/terminal/repl.py` 在 `InlineRepl.__init__()` 中、prompt 输入启动前调用 `detect_terminal_brightness()`。

- Observation: Slash 命令补全已经有可用的数据层和接受行为基础。缺失的是目标视觉布局和更精确的空闲输入控制，而不是命令注册表本身。
  Evidence: `ui/cli/suggestions.py::suggestions_for()` 返回 command、resume session 和 file suggestion。`ui/cli/terminal/completer.py::InlineCompleter` 将这些 suggestion 适配为 prompt_toolkit completion。`ui/cli/terminal/prompt_session.py` 已经围绕高亮 completion 绑定了 `Enter` 和 `Tab`。

- Observation: 运行回合中的 `Ctrl+C` 已经与空闲 prompt 中的 `Ctrl+C` 分离。
  Evidence: `ui/cli/terminal/stream_session.py` 将 `Esc` 和 `Ctrl+C` 都绑定为 streaming preview 的取消操作。`ui/cli/terminal/prompt_session.py` 单独将空闲 prompt 的 `Ctrl+C` 绑定为 `SubmissionKind.CANCEL`。

- Observation: OSC 11 回复中的 `rgb:f8f8/f8f8/f8f8` 通道是多位十六进制值，不能直接按 0..255 通道值计算 luminance。
  Evidence: `ui/cli/terminal/detect.py` 原实现直接 `int(value, 16)` 后传入 `_relative_luminance()`；实现中新增 `_brightness_from_osc11_reply()`，按回复通道宽度归一化到 0..255 后再分类。

## Decision Log（决策记录）

- Decision: 保持运行回合中的 `Ctrl+C` 只用于取消；双击 `Ctrl+C` 退出只应用于 CLI 空闲输入 prompt。
  Rationale: 运行回合已经把 `Ctrl+C` 作为中断 affordance。如果 streaming 时同一个键也可能退出进程，就更容易误终止长任务。
  Date/Author: 2026-06-14 / Codex，经用户确认。

- Decision: 如果空闲输入框里有文本，第一次 `Ctrl+C` 清空文本并显示 `Press Ctrl-C again to exit`；1.5 秒内第二次 `Ctrl+C` 退出。
  Rationale: 用户明确选择了这个行为。它也易于解释，并避免中断后意外提交残留的部分命令文本。
  Date/Author: 2026-06-14 / User and Codex。

- Decision: 用自定义 prompt panel 实现命令补全，而不是依赖 prompt_toolkit 默认 `CompletionsMenu` 作为最终外观。
  Rationale: 现有默认菜单具备功能性补全，但不能保证目标截图中的两列“命令 + 描述”布局。自定义面板可以复用同一 suggestion 数据，同时控制布局、高亮和可见性。
  Date/Author: 2026-06-14 / Codex。

- Decision: 将 OSC 11 泄漏预防视为健壮的终端能力问题，而不是单纯的字符串清理问题。
  Rationale: 从 prompt buffer 过滤泄漏文本可以作为最后防线，但主要修复应该避免终端探测回复残留在输入流中。这样在 Windows Terminal、PowerShell，以及支持或不支持 OSC 11 的终端上都更稳定。
  Date/Author: 2026-06-14 / Codex。

## Outcomes & Retrospective（结果与回顾）

实现已完成自动化验证，但尚未在真实交互式 TTY 中完成手动验收，因此计划仍留在 active 目录。代码改动集中在 CLI 终端层：Windows 上跳过不可靠 OSC 11 查询，保留 `COLORFGBG` / dark fallback；空闲 prompt 的第一次 `Ctrl+C` 会清空输入并显示 `Press Ctrl-C again to exit`，1.5 秒内第二次退出；运行中的 `Ctrl+C` 继续取消 `StreamingSession`；默认 completion menu 已替换为自定义两列 suggestion panel。聚焦测试和 compile 检查已通过。

验证结果：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py -q
    49 passed

    uv run python -m compileall ui services core
    completed without syntax errors

剩余验收项是在真实终端中运行 `uv run python -m ui.cli.app`，按 `Validation and Acceptance` 中的手动步骤确认启动输入框、suggestion panel、Tab/Enter 和空闲/运行中 `Ctrl+C` 行为。

## Context and Orientation（上下文与定位）

OneCode 是一个 Python code-agent runtime。交互式 CLI 只是用户界面层；它不应实现 agent loop 逻辑、工具执行、provider 协议或权限策略。相关 CLI 文件位于 `ui/cli/`。

TTY 入口是 `ui/cli/app.py`。它构建 `CliRuntime`，然后启动 `ui/cli/terminal/repl.py::InlineRepl`。`InlineRepl` 拥有主交互循环。它打印启动 banner，创建用于空闲用户输入的 `PromptSession`，通过 `ui/cli/commands.py` 分发 slash command，并把普通 prompt 交给 agent loop 运行。

空闲输入框位于 `ui/cli/terminal/prompt_session.py`。它使用 prompt_toolkit 这个 Python 终端 UI 库，绘制非全屏输入区域：上边框、一行可编辑 prompt、hint 行和下边框。它返回带有 `SubmissionKind` 值的 `PromptSubmission`，这样 REPL 可以区分普通提交、队列输入、取消和退出。

运行回合预览位于 `ui/cli/terminal/stream_session.py`。它一边消耗 agent streaming events，一边显示 live Markdown 预览文本。它已经将 `Esc` 和 `Ctrl+C` 绑定为取消当前 preview。这是运行回合取消行为的正确位置，应该继续与空闲 prompt 的退出行为分离。

Slash command suggestion 由 `ui/cli/suggestions.py::suggestions_for(runtime, text, cursor)` 生成。对于 `/` 或 `/r` 这样的命令输入，它调用 `ui/cli/commands.py::visible_commands()`，返回包含 `display`、`replacement` 和 `description` 的 `SuggestionItem` 对象。当前适配器 `ui/cli/terminal/completer.py::InlineCompleter` 将这些 item 转换成 prompt_toolkit `Completion` 值。实现应复用这个数据源，以保证命令名和描述始终与 command registry 一致。

终端亮度检测实现在 `ui/cli/terminal/detect.py::detect_terminal_brightness()`。它当前首先尝试 OSC 11 查询。OSC 11 是一个终端转义序列，含义是“你的背景色是什么？” 正确回复可能类似 `ESC ] 11 ; rgb:f8f8/f8f8/f8f8 BEL` 或 `ESC ] 11 ; rgb:f8f8/f8f8/f8f8 ESC \`。当前实现将查询写入 stdout file descriptor，并尝试从同一个 file descriptor 读取回复。在 Windows Terminal + PowerShell 下，该回复可能残留到后续 prompt 输入缓冲里，并以可编辑文本出现。

## Plan of Work（工作计划）

第一步，使终端亮度探测安全。在 `ui/cli/terminal/detect.py` 中保留公开函数 `detect_terminal_brightness(stdout=None, *, timeout=0.15) -> TerminalBrightness`，但调整 OSC 11 路径：只有当实现能够可靠发送查询并消费回复、且不会把字节留给 prompt_toolkit 时，才运行 OSC 11。在 Windows 上，保守默认应跳过 OSC 11，转而使用 `COLORFGBG` 或现有暗色 fallback。这可以避免用户当前环境中的启动输入污染。如果加入健壮的 input-side 实现，它必须从终端输入流读取，而不是从 stdout 读取，并且必须在探测后恢复终端模式。探测保持 best-effort：失败绝不能抛异常，也不能长时间阻塞启动。

在 `ui/cli/terminal/prompt_session.py` 中添加防御性清理 helper，但只用于 initial prompt text 或 incoming raw buffer text 中精确匹配已知 OSC 11 reply fragment 的场景。这个 helper 不应删除任意用户输入。它应识别窄格式，例如带有斜杠分隔十六进制颜色通道、可选字符串终止符的 `]11;rgb:...`。这是兜底机制，用于防止某些终端回复即使经过更安全探测后仍然泄漏。

第二步，在 `ui/cli/terminal/prompt_session.py` 中实现空闲双击 `Ctrl+C` 语义。扩展 `PromptSession`，使它能跨 prompt invocation 记住上一次空闲 `Ctrl+C` 的时间。使用 `time.monotonic()`，避免系统时间变化影响计时。添加类似 `exit_confirm_window_seconds: float = 1.5` 的 constructor 参数，方便测试。第一次空闲 `Ctrl+C` 应清空 `buffer.text`，将短暂 hint 更新为 `Press Ctrl-C again to exit`，存储当前 monotonic timestamp，并保持 prompt application 继续运行。如果窗口期内再次收到 `Ctrl+C`，返回 `PromptSubmission(SubmissionKind.EXIT)`。如果窗口期已过，则把该按键视为新的第一次按下。任何普通文字输入、普通提交或显式退出都应重置 pending exit state。bottom hint 应在窗口过期或用户恢复输入后回到普通 hint。

保持 `ui/cli/terminal/stream_session.py` 中的运行回合取消行为不变。不要让 streaming 状态下的 `Ctrl+C` 退出进程。如果需要触碰这个文件，也应只为了测试或极小的文案一致性。

第三步，在 `ui/cli/terminal/prompt_session.py` 中用自定义命令 suggestion panel 替换默认 completion presentation。如果 `InlineCompleter` 仍对 completion application 有用，可以保留它，但不要依赖 prompt_toolkit 的 `CompletionsMenu` 作为可视列表。自定义 panel 应是 prompt line 下方的 prompt_toolkit `Window`。没有 suggestion 时不渲染任何内容。当用户输入 `/`、`/r` 或其他命令前缀时，它显示有界候选列表。每一行左侧展示 command display text，右侧展示 description。高亮行使用 CLI 现有的 foreground-only 样式约定，以便在明暗终端上都可读。避免设置全局背景色。panel 应保持紧凑，例如最大高度为 8 行，与当前 `CompletionsMenu(max_height=8)` 行为一致。

自定义 panel 应保留这些按键语义。Up 和 Down 移动高亮 suggestion。Tab 将高亮 suggestion 应用到输入 buffer，但不提交。Enter 应用并提交高亮 suggestion。如果有 suggestion 但没有显式高亮，则把第一条 suggestion 当作高亮项，这与当前行为一致。如果没有 suggestion，Enter 提交原始输入文本，Tab 启动或刷新 suggestion 计算。对于 `/resume ` 参数 suggestion 和 `@file` suggestion，除非文件 suggestion 在两列 command panel 中显示效果很差，否则保留相同语义。必要时用同一通用 panel 渲染：command suggestion 显示 description，file suggestion 显示路径以及 "File" 或 "Directory" metadata。

第四步，更新测试。在 `tests/test_cli_terminal.py` 中添加测试，通过 pipe input 驱动 `PromptSession`，证明一次空闲 `Ctrl+C` 不退出、1.5 秒内两次空闲 `Ctrl+C` 会退出、延迟后的第二次 `Ctrl+C` 不退出、第一次 `Ctrl+C` 会清空文本。延迟测试不应真实 sleep 1.5 秒；应注入 fake monotonic clock 或使用很小的可配置窗口。添加测试证明运行中的 `StreamingSession` 仍将 `Ctrl+C` 视为取消，类似现有 `Esc` 取消测试。如果自定义 suggestion panel 的渲染被拆成纯 helper，则为它的 selection state 添加测试。继续通过 `prompt_toolkit.input.create_pipe_input()` 和 `prompt_toolkit.output.DummyOutput` 保持终端测试 headless。

在 `tests/test_cli_prompt_input_suggestions.py` 中，保留现有 suggestion provider 测试，并添加缺失的命令过滤用例，例如 `/r` 返回 `/resume` 且不返回无关命令。不要让这些 provider 测试依赖终端渲染。

在终端检测测试文件中，或者如果没有单独文件则放在 `tests/test_cli_terminal.py` 中，为 `detect_terminal_brightness()` 添加测试：在类 Windows 条件下，OSC 11 probe 应被跳过，并由 `COLORFGBG` 或 fallback 决定结果；当显式测试 OSC 11 路径时，已知 OSC 11 回复 parser 仍应正确分类明暗。如果需要模拟平台，将 probe decision 提取成一个小型 pure helper，以便不改变真实 OS 也能测试。

最后，运行聚焦验证并做一次手动交互检查。聚焦测试应包括 `uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py -q`。如果终端检测测试放在新文件中，也将该文件加入同一命令。运行 `uv run python -m compileall ui services core`，或者运行更窄的项目 compile 命令。然后从仓库根目录启动 `uv run python -m ui.cli.app`，验证用户可见行为。

## Concrete Steps（具体步骤）

从仓库根目录工作：

    D:\study\OneCode

编辑前先检查当前状态：

    git status --short
    rg -n "detect_terminal_brightness|_OSC11_REQUEST|CompletionsMenu|Keys.ControlC|suggestions_for|InlineCompleter" ui\cli tests

以小步实现变更。每一步后运行聚焦测试，不要等到最后才测试：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py -q

终端检测变更后，如果测试添加在单独文件中，也包括它：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py tests/test_cli_terminal_detect.py -q

运行 compile 检查：

    uv run python -m compileall ui services core

手动验证时启动 CLI：

    uv run python -m ui.cli.app

实现完成后的预期手动观察结果：

    Startup: 输入框为空；不包含 ]11;rgb:f8f8/f8f8/f8f8\ 或任何 OSC 11 回复文本。
    Type /: 输入行下方出现命令列表，包含命令名和描述。
    Type /r: 列表缩小到 /resume 等命令。
    Press Tab with /r: 输入框填入 /resume，但不提交。
    Press Enter while /resume is highlighted: 命令被接受并提交。
    While idle, type abc and press Ctrl+C once: abc 消失，hint 显示 Press Ctrl-C again to exit。
    Press Ctrl+C again within 1.5 seconds: CLI flush 状态后退出。
    Start a normal agent turn and press Ctrl+C while it is streaming: 当前回合被取消，进程保持打开。

## Validation and Acceptance（验证与验收）

只有自动化和手动行为都证明功能有效时，才接受实现。

自动化验收：

从 `D:\study\OneCode` 运行：

    uv run python -m pytest tests/test_cli_terminal.py tests/test_cli_prompt_input_suggestions.py -q

预期这些文件中的所有测试通过。新增测试必须证明：空闲双击 `Ctrl+C` 会退出，第一次空闲 `Ctrl+C` 会清空文本并显示确认 hint，延迟后的第二次 `Ctrl+C` 不退出，运行中的 `Ctrl+C` 只取消活动回合，以及命令 suggestion 保持 provider 行为。

如果创建了单独的终端检测测试文件，运行：

    uv run python -m pytest tests/test_cli_terminal_detect.py -q

预期测试证明：类 Windows 环境不会使用会泄漏的 OSC 11 路径，并且显式 OSC 11 回复解析仍然有效。

运行：

    uv run python -m compileall ui services core

预期 compileall 无语法错误完成。

手动验收：

启动：

    uv run python -m ui.cli.app

CLI 必须以干净输入框启动。它不得显示 `]11;rgb:f8f8/f8f8/f8f8\`、`]11;rgb:` 或任何其他可见 OSC 11 回复。输入 `/` 必须在输入行下方显示紧凑 suggestion panel。输入 `/r` 必须缩小列表。`Tab` 必须填入高亮 completion 且不提交。`Enter` 必须接受并提交高亮 completion。空闲时，第一次按 `Ctrl+C` 必须清空当前输入并显示 `Press Ctrl-C again to exit`；1.5 秒内再次按下必须退出。运行回合中按 `Ctrl+C` 必须取消该回合并保持 CLI 打开。

## Idempotence and Recovery（幂等性与恢复）

实现应可安全重复执行。测试创建临时目录，不应要求仓库外的持久状态。手动 CLI 运行会按正常 runtime 行为创建或更新 `.onecode/<session_id>/` session artifact；这是预期行为，不属于此功能的源代码变更。

如果 OSC 11 实现在不同终端上表现不同，应优先选择保守 fallback，而不是复杂探测。无法被安全查询的终端应使用 `COLORFGBG` 或暗色 fallback，而不是冒着控制序列文本泄漏进输入 buffer 的风险。如果自定义 completion panel 导致布局回归，应在迭代渲染时保持现有 suggestion provider 和 key-binding 行为不变。避免修改 `core/loop.py` 或 services 层代码来修 UI 行为；这些层不负责终端输入。

如果通过 pipe input 驱动 prompt_toolkit 的测试挂住，先把测试缩小到 pure helper。例如，将双击 `Ctrl+C` 计时拆成无需启动 application 的 helper 测试，然后只保留一个 integration-style pipe-input 测试覆盖端到端 key binding。

## Artifacts and Notes（产物与笔记）

实现前收集到的关键现有代码证据：

    ui/cli/terminal/repl.py: InlineRepl.__init__ 在创建 PromptSession 前调用 detect_terminal_brightness()。
    ui/cli/terminal/detect.py: _OSC11_REQUEST 是 b"\x1b]11;?\x07"；解析匹配 "\x1b]11;rgb:..." 的回复。
    ui/cli/terminal/prompt_session.py: PromptSession 当前使用 CompletionsMenu(max_height=8)，并将空闲 Ctrl+C 绑定为 SubmissionKind.CANCEL。
    ui/cli/terminal/stream_session.py: StreamingSession 将 Esc 和 Ctrl+C 绑定为运行回合中的取消操作。
    ui/cli/suggestions.py: suggestions_for() 提供 command、/resume 和 @file suggestion。
    ui/cli/commands.py: visible_commands() 是用户可见 slash command 及其描述的事实来源。

目标空闲控制行为：

    First idle Ctrl+C:
      清空输入
      显示 "Press Ctrl-C again to exit"
      保持 CLI 打开

    Second idle Ctrl+C within 1.5 seconds:
      返回 SubmissionKind.EXIT
      让 InlineRepl flush transcript、trace、errors 和 MCP transports
      正常退出进程

    Running Ctrl+C:
      取消活动 StreamingSession
      打印现有取消提示
      保持 CLI 打开

## Interfaces and Dependencies（接口与依赖）

使用 CLI 已经依赖的 prompt_toolkit。不要引入新的终端 UI 框架。

除非有充分理由，否则保持以下公开或半公开接口稳定：

    ui.cli.terminal.detect.detect_terminal_brightness(stdout=None, *, timeout=0.15) -> Literal["light", "dark"]
    ui.cli.terminal.prompt_session.PromptSession.read(... ) -> PromptSubmission
    ui.cli.terminal.prompt_session.PromptSubmission(kind: SubmissionKind, text: str = "")
    ui.cli.terminal.prompt_session.SubmissionKind.SUBMIT
    ui.cli.terminal.prompt_session.SubmissionKind.QUEUE
    ui.cli.terminal.prompt_session.SubmissionKind.CANCEL
    ui.cli.terminal.prompt_session.SubmissionKind.EXIT
    ui.cli.suggestions.suggestions_for(runtime, text, cursor) -> tuple[SuggestionItem, ...]

可以在 `ui/cli/terminal/prompt_session.py` 内添加 helper 类型，例如用于 prompt hint 的小状态对象，以及计算当前 suggestion panel rows 的 pure helper。如果 helper 变大或需要共享，可以在 `ui/cli/terminal/` 下创建新文件，例如 `suggestion_panel.py`，但它必须保持 UI-only，并以 `SuggestionItem` 为数据来源。

最终实现不应修改 `core/loop.py`、tool descriptor、provider adapter、permission policy 或 services 层模块。本工作只改进 CLI 输入与终端渲染。

Revision note, 2026-06-14 / Codex: 初始 ExecPlan 在研究和用户确认后创建。它记录了已达成一致的空闲 `Ctrl+C` 行为、健壮 OSC 11 方向和自定义 completion panel 实现方式。

Revision note, 2026-06-14 / Codex: 将 ExecPlan 正文翻译为中文，同时保留 `PLANS.md` 要求的英文 living section 名称，方便后续实现者继续按仓库规范维护。
