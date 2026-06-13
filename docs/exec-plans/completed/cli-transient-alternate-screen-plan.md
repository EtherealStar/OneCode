# CLI 临时界面使用备用屏幕并在退出后恢复

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文遵守仓库根目录 `PLANS.md`。后续实现者修改本文时，必须保持本文自包含：只阅读本文和当前工作区，就能完成实现、验证和恢复。

## Purpose / Big Picture

完成本计划后，OneCode CLI 中用于查看状态、历史、列表和交互选择的临时界面会在退出后自动消失，不再停留在终端滚动历史中。用户运行 `uv run python -m ui.cli.app` 后，输入 `/usage`、`/status`、`/tasks`、`/mcp`、`/resume` 等命令进入查看界面，按 `Esc` 返回时应看到进入界面前的主终端内容，查看页本身不会残留。普通对话输出、模型回答、工具结果摘要、`/clear` 这种短反馈仍会保留在主终端历史中，方便用户回看。

本计划要修正的是 CLI 终端呈现层的抽象，而不是给每个命令分别增加清屏逻辑。实现应引入终端的备用屏幕缓冲区，英文常称 alternate screen buffer。备用屏幕是现代终端支持的一种模式：程序发送 `\x1b[?1049h` 进入一块临时屏幕，发送 `\x1b[?1049l` 退出并恢复进入前的主屏幕。备用屏幕里的内容会被丢弃，因此适合状态页、选择器和临时模态界面。

2026-06-12 追加要求：本计划不再接受“临时界面无法进入备用屏幕时打印到主屏幕”的旧 fallback。临时界面包括 page、selector、confirm 和 `/connect` 多步骤 flow；这些界面必须只渲染到 transient terminal surface。若当前 `sys.stdout` 被宿主代理而 `stdout.isatty()` 为 false，CLI 仍应把备用屏幕控制码和页面内容写入当前 `sys.stdout`，因为这是宿主管理的同一个可见终端缓冲区。Windows 的 `CONOUT$` 或 POSIX 的 `/dev/tty` 只能作为读取终端尺寸的辅助句柄，不能作为默认渲染目标，否则会绕过宿主的 PTY/scrollback，导致 `Esc` 退出无法恢复实际写入页面的屏幕。若没有可写 stdout 或没有交互输入，应返回明确错误，不得把 page 内容 inline 打印到主屏幕 scrollback。普通主 prompt 和 batch 输入仍可保留自己的非交互路径，因为它们不是临时界面。

## Progress

- [x] (2026-06-12 16:57 Asia/Shanghai) 阅读 `PLANS.md`，确认 ExecPlan 必须自包含、包含 living document 四个固定章节，并且写入 Markdown 文件时不包外层代码块。
- [x] (2026-06-12 16:58 Asia/Shanghai) 阅读当前 CLI 相关代码和设计文档，确认 `ui/cli/prompt_input/terminal.py` 是终端绘制入口，`ui/cli/prompt_input/session.py` 是 page、selector、confirm 和 prompt session 的统一运行入口。
- [x] (2026-06-12 16:59 Asia/Shanghai) 记录用户反馈：问题不只发生在 `/resume`，`/usage`、`/status`、`/tasks` 等所有显示临时界面的命令都应在退出后恢复主屏幕。
- [x] (2026-06-12 17:00 Asia/Shanghai) 记录参考实现要点：使用 DEC 私有模式 1049，进入备用屏幕必须发生在首帧渲染之前，异常卸载时必须防御性退出备用屏幕。
- [x] (2026-06-12 17:02 Asia/Shanghai) 创建本 ExecPlan，规定从终端 surface 抽象统一修复，不改业务命令逐个清屏。
- [x] (2026-06-12 Asia/Shanghai) 在 `ui/cli/prompt_input/terminal.py` 中为 `TerminalDriver` 增加 DEC 1049 备用屏幕控制码和幂等 `enter_alternate_screen()`、`exit_alternate_screen()`、`clear_and_home()` 方法；非 TTY 不发送备用屏幕控制码。
- [x] (2026-06-12 Asia/Shanghai) 修改 `ui/cli/prompt_input/session.py::_run_session()`，新增 `surface` 参数；page、select 和 confirm 默认使用 alternate surface，并保证进入备用屏幕发生在首帧 `driver.render(state)` 之前，退出发生在 `finally` 中。
- [x] (2026-06-12 Asia/Shanghai) 统一修正 `/resume [target]` 和 selector 恢复后的历史展示：`ui/cli/commands.py::_resume()` 与 `ui/cli/app.py::_resume_history_result()` 都返回 `presentation="page"`。
- [x] (2026-06-12 Asia/Shanghai) 为 `/connect` 多步骤交互实现 `transient_terminal_scope()`，整个 provider 选择、base URL、API key 和 model 输入只进入一次备用屏幕，内部步骤复用同一个 transient scope。
- [x] (2026-06-12 Asia/Shanghai) 增加终端 surface、page、selector、confirm、resume 和 connect shared scope 测试，覆盖首帧前进入、异常退出恢复、非 TTY fallback、主 prompt 不进入备用屏幕、`/resume [target]` page 契约。
- [x] (2026-06-12 Asia/Shanghai) 更新 `docs/design-docs/cli-architecture.md`，说明临时界面通过备用屏幕呈现，普通对话输出继续留在主屏幕，并记录 `/connect` 使用 shared transient scope。
- [x] (2026-06-12 Asia/Shanghai) 运行完整 CLI 相关测试、compile check 和全量测试，并在本文记录最终结果。
- [x] (2026-06-12 Asia/Shanghai) 根据真实手动验证反馈更新计划：旧 fallback 会导致 `/status`、`/usage` 的 Rich panel 仍进入主屏幕历史，且 stdout 非 TTY 时高度退回 24 行；新的实现目标是删除临时 UI inline fallback，引入真实终端设备层。
- [x] (2026-06-12 Asia/Shanghai) 重构 `ui/cli/prompt_input/terminal.py`，引入 transient terminal device：渲染目标保持当前 stdout；Windows `CONOUT$` 和 POSIX `/dev/tty` 只作为尺寸查询辅助句柄。
- [x] (2026-06-12 Asia/Shanghai) 删除 `show_page()`、`select_item()`、`read_confirm_sync()` 中把临时 UI 打印到主屏幕的 fallback；找不到 transient terminal 时返回明确错误。
- [x] (2026-06-12 Asia/Shanghai) 修正 page/selector 高度计算，使内容区高度基于真实终端行数和 header/footer 精确预留，终端足够高时短页面完整展开。
- [x] (2026-06-12 Asia/Shanghai) 更新测试，覆盖 `stdin.isatty=True` 但 `stdout.isatty=False` 时仍使用真实 transient output，找不到 terminal output 时不打印 page 内容，短 page 不显示 scroll footer。
- [x] (2026-06-12 Asia/Shanghai) 重新运行 CLI focused tests、compile check 和全量测试，均通过。

## Surprises & Discoveries

- Observation: `/status`、`/usage`、`/memory`、`/permissions`、`/skills`、`/tasks` 和 `/mcp` 在命令层已经返回 `presentation="page"`，但 page mode 当前仍只是主屏幕上的可滚动文本，不是备用屏幕。
  Evidence: `ui/cli/commands.py` 中这些 handler 返回 `CommandResult(..., presentation="page")`；`ui/cli/app.py::main_loop_async()` 对 page 调用 `show_page(result.renderable)`；`ui/cli/prompt_input/session.py::show_page()` 最终进入 `_run_session()`，但 `TerminalDriver` 只使用 `\r`、`\x1b[J` 和上移光标重绘当前主屏幕区域。

- Observation: `/resume [target]` 的当前行为与 CLI 设计文档不一致。设计文档说恢复后打开历史 page，但代码和测试把目标恢复历史作为 inline 输出处理。
  Evidence: `ui/cli/commands.py::_resume()` 恢复成功后返回 `CommandResult(runtime=resumed, renderable=renderer.render_group(...))`，没有设置 `presentation="page"`。`tests/test_cli_resume.py::test_resume_command_replaces_runtime_and_restores_messages` 断言 `result.presentation == "inline"`，并断言输出包含 `[read_file call_read ok]`。

- Observation: 现有 `_run_session()` 已经是临时交互的共同入口，适合集中接入备用屏幕生命周期。
  Evidence: `ui/cli/prompt_input/session.py` 中 `read_prompt()`、`read_text_sync()`、`read_confirm_sync()`、`select_item()` 和 `show_page()` 都调用 `_run_session()`；`_run_session()` 负责 hide/show cursor、读取按键、调用 reducer 和渲染状态。

- Observation: 进入备用屏幕必须发生在首帧渲染之前，否则第一帧可能先写入主屏幕，退出后残留一帧破损界面。
  Evidence: 用户提供的参考实现说明 React 版本使用 `useInsertionEffect`，目的就是早于首次 render 写入 `ENTER_ALT_SCREEN`。OneCode 对应位置是 `_run_session()` 中 `driver.render(state)` 之前。

- Observation: `/connect` 的 shared scope 不能只保存一个“已经在 transient 中”的标记，否则内部 `select_item()` 和 `read_text()` 会跳过独立进入备用屏幕，但外层没有真正进入备用屏幕。
  Evidence: 最终实现让 `transient_terminal_scope()` 在最外层调用 `driver.enter_alternate_screen()` 和 `driver.exit_alternate_screen()`，同时通过 `ContextVar` 记录嵌套深度；内部 alternate session 检测到 scope active 后只调用 `driver.clear_and_home()` 清理临时屏幕并渲染当前步骤。

- Observation: public entry points 会先创建一个 driver 做 TTY/高度判断，再在 `_run_session()` 中创建实际渲染 driver。
  Evidence: `show_page()` 和 `select_item()` 都调用 `_default_driver("")` 检查 `is_interactive` 或 `content_height`，随后 `asyncio.to_thread(_run_session, ...)` 里 `_run_session()` 再调用 `_default_driver(prompt)`。测试 `test_public_page_selector_and_confirm_entry_points_use_alternate_screen` 因此为每个 public call 提供同一个 fake driver 两次。

- Observation: 真实终端中 `stdin` 可以是 TTY，但 `stdout` 可能被宿主代理为非 TTY；旧实现因此把 page 命令降级为主屏 Rich print。
  Evidence: 用户截图中 `/status` 和 `/usage` 的 Rich Panel 保留在主屏幕历史，且没有 `prompt_input` page mode 的 `Esc return` footer。诊断命令显示 `stdin True`、`stdout False`、`shutil.get_terminal_size((100,24))` 返回 fallback `100x24`。

- Observation: 当前 `content_height` 使用 `shutil.get_terminal_size((100, 24)).lines - 6`，stdout 非 TTY 时会固定按 24 行计算，即使实际终端窗口更高也会误判需要滚动。
  Evidence: `ui/cli/prompt_input/terminal.py::TerminalDriver.content_height` 使用全局 terminal size fallback，不指定真实 output fd；截图中实际窗口高度充足，但短页面仍按受限高度渲染。

- Observation: 打开 Windows `CONOUT$` 作为默认 transient 输出目标仍然会让页面残留在当前会话显示中。
  Evidence: 用户二次截图显示 `/status` 按 `Esc` 后仍残留在当前终端视图中。原因是宿主显示和 scrollback 绑定的是当前 stdout/PTY，`CONOUT$` 不是同一个被宿主管理的输出流；备用屏幕 enter/exit 必须写入页面内容实际写入的同一个流。

## Decision Log

- Decision: 不按命令名逐个修补清屏，而是在 `prompt_input` 终端层引入统一的 transient surface。
  Rationale: `/usage`、`/status`、`/resume`、selector、confirm 和 future page 都有同一类问题。逐个命令清屏会重复、脆弱，并且无法可靠处理长内容、滚动、窗口高度变化和异常退出。统一 surface 能让命令层只表达 “page 还是 inline”，终端层负责恢复主屏幕。
  Date/Author: 2026-06-12 / Codex

- Decision: interactive TTY 下的临时查看界面使用 DEC private mode 1049，即 `\x1b[?1049h` 和 `\x1b[?1049l`。
  Rationale: 1049 是现代终端普遍支持的备用屏幕模式，进入时保存主屏幕并切换到临时屏幕，退出时恢复主屏幕并丢弃临时屏幕。它比“清当前屏幕区域”更符合用户预期，也能处理内容超过一屏的 page。
  Date/Author: 2026-06-12 / Codex

- Decision: `read_prompt()` 继续使用 inline surface，不进入备用屏幕。
  Rationale: 主 prompt 和普通对话是 OneCode 的主交互流，用户应能用终端滚动历史回看模型回答和工具摘要。备用屏幕只用于可退出的临时查看或交互界面。
  Date/Author: 2026-06-12 / Codex

- Decision: page、select 和 confirm 默认使用 alternate screen；普通 text/password 输入先保持 inline，除非由更高层 transient interaction scope 包裹。
  Rationale: page、selector 和 confirm 都是临时界面，退出后不应污染 scrollback。text/password 输入本身只有一两行，单独使用备用屏幕会过重；但 `/connect` 这种多步骤 flow 需要统一进入一次备用屏幕，因此应提供 scope 机制而不是让每个输入步骤独立切换。
  Date/Author: 2026-06-12 / Codex

- Decision: `/resume [target]` 恢复成功后的历史展示必须改为 `presentation="page"`。
  Rationale: 恢复历史是查看界面，不是短反馈。当前 inline 行为既违反设计文档，也直接导致历史残留在终端滚动历史中。
  Date/Author: 2026-06-12 / Codex

- Decision: `transient_terminal_scope()` 使用 `ContextVar` 记录嵌套深度，并由外层 scope 真正进入和退出备用屏幕。
  Rationale: `read_text()` 等同步读取通过 `asyncio.to_thread()` 执行，Python 会把当前 contextvars context 传播到 worker thread；这样 `/connect` 的外层 async scope 可以被内部同步 `_run_session()` 可靠看见。thread-local 在这里更容易因为线程切换失效。
  Date/Author: 2026-06-12 / Codex

- Decision: scope 内每个 alternate 子 session 首帧前调用 `clear_and_home()`，而不是用主屏幕局部重绘来覆盖上一步。
  Rationale: provider selector、API key 和 model 输入内容高度不同，复用同一个备用屏幕时必须先清屏回左上角，避免上一步的长内容残留在临时屏幕里。
  Date/Author: 2026-06-12 / Codex

- Decision: 删除临时 UI 的 inline fallback；page、selector、confirm 和 `/connect` 必须使用 transient terminal surface，找不到真实终端时返回错误。
  Rationale: fallback 会把临时界面永久写入 scrollback，正是本计划要消除的旧界面实现。保留 fallback 会让真实宿主环境里 `stdout.isatty()` 误判继续绕过备用屏幕。
  Date/Author: 2026-06-12 / Codex

- Decision: transient UI 渲染必须写入当前 stdout，即使 `stdout.isatty()` 为 false；`CONOUT$`/`/dev/tty` 不作为默认输出目标。
  Rationale: 备用屏幕只对同一个终端缓冲区生效。当前 stdout 是主屏输出和宿主 scrollback 的事实来源；把页面写到 `CONOUT$` 可能绕过宿主 PTY，导致退出备用屏幕无法恢复页面实际写入的可见缓冲区。
  Date/Author: 2026-06-12 / Codex

- Decision: page 和 selector 高度计算必须基于真实 terminal output handle，并按 UI 元素精确扣除 title/footer，而不是统一 `lines - 6`。
  Rationale: 统一扣 6 行让短页面在高窗口里仍可能误判为可滚动；使用真实 fd 尺寸和明确 header/footer 预算才能让终端足够大时完整展开。
  Date/Author: 2026-06-12 / Codex

## Outcomes & Retrospective

截至 2026-06-12，第一版实现已完成但真实手动验证发现仍不充分。修改集中在 `ui/cli/prompt_input/terminal.py`、`ui/cli/prompt_input/session.py`、`ui/cli/connect.py`、`ui/cli/commands.py`、`ui/cli/app.py`、`tests/test_cli_prompt_input_terminal.py`、`tests/test_cli_connect.py`、`tests/test_cli_resume.py` 和 `docs/design-docs/cli-architecture.md`。`/connect` shared scope 已完成，主 prompt 仍使用 inline surface。第一版保留的非 TTY fallback 被确认是错误方向：在 stdout 被宿主代理时，page 会直接打印到主屏幕历史。下一步必须删除临时 UI fallback，并用真实 terminal device 支撑备用屏幕。第一版曾通过 focused 验证：

    uv run python -m pytest tests/test_cli_prompt_input_terminal.py tests/test_cli_connect.py tests/test_cli_resume.py -q
    28 passed in 1.58s

随后完成计划中的完整验证：

    uv run python -m pytest tests/test_cli_prompt_input_terminal.py tests/test_cli_prompt_input_state.py tests/test_cli_pages.py tests/test_cli_resume.py tests/test_cli_commands.py tests/test_cli_connect.py tests/test_cli_mcp_trust_prompt.py -q
    66 passed in 3.53s

    uv run python -m compileall ui services core
    completed successfully

    uv run python -m pytest tests -q
    396 passed in 15.07s

第一版自动化验收不足以覆盖真实宿主 stdout 代理问题。最终验收现在改为：interactive stdin 存在且能打开真实 terminal output 时，page/select/confirm 必须进入备用屏幕；找不到真实 terminal output 时只允许返回明确错误；不允许把临时 UI 内容作为 fallback 打印进主屏幕 scrollback。

2026-06-12 第二轮实现完成。`ui/cli/prompt_input/terminal.py` 现在提供 `TerminalDevice` 和 `TransientTerminalUnavailable`；transient device 不再要求 `stdout.isatty()`，只要求输入可交互且 stdout 可写。备用屏幕控制码和页面内容写入当前 stdout，保证 enter/exit 和页面内容作用在同一个宿主终端缓冲区。`CONOUT$` 或 `/dev/tty` 仅用于辅助读取尺寸，读取失败时使用较高的尺寸 fallback，避免短页面被 24 行默认值强制滚动。`ui/cli/prompt_input/session.py` 删除了 page/select/confirm 的 inline fallback，并让 `/connect` scope 复用同一个 transient driver。`ui/cli/app.py` 和 `ui/cli/permissions.py` 捕获 transient terminal 不可用错误，只显示简短错误或 deny 权限请求，不打印临时界面内容。

最终验证：

    uv run python -m pytest tests/test_cli_prompt_input_terminal.py tests/test_cli_prompt_input_state.py tests/test_cli_pages.py tests/test_cli_resume.py tests/test_cli_commands.py tests/test_cli_connect.py tests/test_cli_mcp_trust_prompt.py -q
    69 passed in 1.66s

    uv run python -m compileall ui services core
    completed successfully

    uv run python -m pytest tests -q
    399 passed in 13.62s

## Context and Orientation

OneCode 是一个 Python code agent runtime。CLI 位于 `ui/cli/`，负责应用装配、交互输入、命令处理、权限提示和终端渲染。agent 主循环不应知道备用屏幕或 page mode；本计划只修改 CLI 终端 UI 层。

当前 CLI 的关键文件如下。

`ui/cli/app.py` 是 CLI 主循环。`main_loop_async()` 读取用户输入，如果输入是 slash command，就调用 `dispatch_command()`。当命令结果的 `presentation` 是 `"page"` 时，它调用 `show_page(result.renderable)`。当命令结果是普通 inline 输出时，它调用 `renderer.print_renderable(...)` 或直接打印字符串。普通用户 prompt 会调用 `runtime.loop.stream(...)` 并把模型 delta 和工具结果摘要直接打印到主屏幕。

`ui/cli/commands.py` 是 slash command 注册和分发处。它定义 `/status`、`/usage`、`/memory`、`/permissions`、`/skills`、`/tasks`、`/mcp`、`/compact`、`/resume`、`/connect`、`/clear` 和 `/exit`。其中状态类命令已经返回 `presentation="page"`。`/resume [target]` 当前恢复后返回 inline，这是本计划要修正的契约之一。

`ui/cli/types.py` 定义 `CommandResult`。`CommandResult.presentation` 当前只能是 `"inline"` 或 `"page"`。本计划不要求改变这个 public command API；它仍足够表达“普通输出”和“临时查看页”的区别。

`ui/cli/prompt_input/session.py` 提供交互 session 的入口：`read_prompt()`、`read_text()`、`read_confirm()`、`select_item()` 和 `show_page()`。这些函数最终调用 `_run_session()`。`_run_session()` 是最适合接入备用屏幕的地方，因为它已经统一管理 hide/show cursor、渲染、读取按键和 `finally` 恢复 cursor。

`ui/cli/prompt_input/terminal.py` 是终端 I/O adapter。`TerminalDriver.render()` 调用 `render_state_text()` 把 `PromptInputState` 转成文本，然后 `_replace_rendered_text()` 用 ANSI 清屏到屏尾和上移光标来更新当前画面。这个机制适合主屏幕上的小范围重绘，但不适合“退出后完全消失”的全屏临时界面。

`ui/cli/prompt_input/state.py` 定义 `PromptInputState`。它的 `mode` 可以是 `"prompt"`、`"text"`、`"password"`、`"select"`、`"confirm"` 或 `"page"`。本计划中的 “page” 指可滚动查看页；“select” 指方向键选择列表；“confirm” 指确认面板；“prompt” 指 OneCode 主输入行。

“TTY” 是终端设备的简称。旧实现要求 `stdin` 和 `stdout` 都是交互式终端，但这在 Codex/PowerShell 等宿主中不可靠，因为 stdout 可能被代理为非 TTY，而进程仍能通过真实控制台输出。新的临时 UI 判断分成两件事：输入必须能交互读取键盘；输出必须能解析到真实终端输出。Windows 下真实终端输出优先尝试 `CONOUT$`；POSIX 下优先尝试 `/dev/tty`。如果二者都不可用，临时 UI 不得 fallback 到主屏打印。

“备用屏幕” 是终端的一种临时缓冲区。程序向 stdout 写入 `\x1b[?1049h` 后，终端保存当前主屏幕并切换到一块新的临时屏幕。程序写入 `\x1b[?1049l` 后，终端恢复主屏幕并丢弃临时屏幕内容。为了确保临时屏幕从空白开始，进入后应立即写 `\x1b[2J\x1b[H`，其中 `\x1b[2J` 清屏，`\x1b[H` 把光标移动到左上角。

## Plan of Work

第一阶段在终端层引入真实 terminal device 和 surface 抽象。Surface 在本文中指“当前交互 session 渲染到哪里以及如何退出”的小对象。它不是 UI 组件，也不读模型或执行命令。真实 terminal device 负责找到交互输入和真实输出：默认主 prompt 可以继续用 `sys.stdin`/`sys.stdout`；临时 UI 必须使用 `TerminalDevice.for_transient()` 或等价构造函数。该构造函数在 `stdin` 不可交互时失败；在 `stdout` 不是 TTY 时尝试打开 Windows `CONOUT$` 或 POSIX `/dev/tty`；仍失败时抛出明确异常，例如 `TransientTerminalUnavailable`。

新增常量：

    ENTER_ALT_SCREEN = "\x1b[?1049h"
    EXIT_ALT_SCREEN = "\x1b[?1049l"
    CLEAR_AND_HOME = "\x1b[2J\x1b[H"

新增类型建议命名为 `TerminalDevice`，可在 `ui/cli/prompt_input/terminal.py` 中实现。它至少保存 `stdin`、`stdout` 和是否拥有 stdout handle。`TerminalDriver` 接收 `device` 或 `stdin`/`stdout`。临时 UI 不再检查 `stdout.isatty()` 来决定 fallback，而是在构造 transient device 时解析真实输出。无论采用哪种结构，都必须满足：进入备用屏幕是幂等的；退出备用屏幕是幂等的；未进入时退出不会写多余控制码；找不到真实终端时抛错，不打印页面；异常路径通过 `finally` 退出备用屏幕并关闭拥有的 terminal output handle。

第二阶段修改 `_run_session()`。给 `_run_session()` 增加一个参数，例如 `surface: Literal["inline", "alternate"] = "inline"` 或 `use_alternate_screen: bool = False`。调用顺序必须改成：

    driver = _default_driver(prompt)
    state = initial_state
    driver.hide_native_cursor()
    try:
        if surface == "alternate":
            driver.enter_alternate_screen()
        driver.render(state)
        ...
    finally:
        if surface == "alternate":
            driver.exit_alternate_screen()
        driver.show_native_cursor()

`driver.enter_alternate_screen()` 不应再静默跳过。对于 transient driver，进入失败就是错误；对于 inline driver，根本不调用该方法。关键约束是 `enter_alternate_screen()` 必须发生在第一次 `driver.render(state)` 之前，避免首帧写入主屏幕。

第三阶段调整 session entry points 的默认 surface。`read_prompt()` 必须继续用 inline surface，并可保留 batch input 路径。`show_page()` 应用 alternate surface，且不得再 `renderer.print_renderable(renderable)`。`select_item()` 应用 alternate surface，找不到 transient terminal 时应抛出或返回明确错误，不得静默返回 `None`。`read_confirm_sync()` 应用 alternate surface，因为权限确认和 MCP trust prompt 都是临时确认界面，退出后不应留下整块确认 UI。`read_text_sync()` 和 password 输入单独调用时继续保持 inline；在 `/connect` transient scope 内必须复用同一个备用屏幕。

第四阶段处理多步骤交互 flow。`/connect` 位于 `ui/cli/connect.py`，它会先 `select_item()` 选择 provider，再用 `read_text()` 读取 base URL、API key 和 model。若直接让 `select_item()` 单独进入备用屏幕，后续 text/password 输入会回到主屏幕，用户体验割裂，也可能出现闪烁。因此需要在 `prompt_input.session` 增加一个共享 transient scope。建议接口为：

    @contextmanager
    def transient_terminal_scope() -> Iterator[None]:
        ...

或者 async 版本：

    async def run_transient_interaction(factory: Callable[[], Awaitable[T]]) -> T:
        ...

实现方式应尽量简单。可以在 `session.py` 中维护一个当前线程或上下文变量，表示已有 alternate surface active。`select_item()`、`read_confirm_sync()` 和未来 text/password 可以复用已有 surface，而不是重复 enter/exit。第一版也可以只把 `/connect` 保持现状，在 `Progress` 中明确“connect shared scope 未完成”，但最终验收应覆盖它，因为用户明确说所有显示界面的命令和交互都要恢复。

第五阶段修正 command presentation。`ui/cli/commands.py::_resume()` 在成功恢复 target 后应返回 `presentation="page"`。`ui/cli/app.py::_resume_history_result()` 也应返回 `presentation="page"`。如果恢复成功提示需要保留，可以继续把 `renderer.render_resume(...)` 和 `renderer.render_restored_messages(...)` 组合为 page 内容。多匹配 session 列表已经是 page，应自动受 `show_page()` 的 alternate surface 保护。

第六阶段补充测试。测试应覆盖两类事实：低层终端控制码和命令层 page 契约。低层测试放在 `tests/test_cli_prompt_input_terminal.py` 或新建 `tests/test_cli_prompt_input_surface.py`。使用 `StringIO` 和 fake TTY stream 验证 interactive 时输出包含 `\x1b[?1049h`、`\x1b[2J\x1b[H` 和 `\x1b[?1049l`，且 enter 序列出现在首帧文本之前，exit 序列出现在结束之后。也要验证 KeyboardInterrupt 或 reducer cancel 时仍输出 exit 序列和 show cursor。命令层测试更新 `tests/test_cli_resume.py`，让 `/resume [target]` 断言 `presentation == "page"`。

第七阶段更新文档。`docs/design-docs/cli-architecture.md` 的 Page Mode 段落应改为说明：page、selector 和确认面板只通过备用屏幕显示；按 Esc 或完成选择后恢复主屏幕；找不到真实 transient terminal 时显示明确错误，不 inline 打印 page。文件职责表可增加 `TerminalDevice` 或 `prompt_input/surface.py`，如果最终新增该文件。

## Concrete Steps

所有命令都在仓库根目录运行：

    cd D:\study\OneCode

开始前检查工作区，确认不要覆盖无关改动：

    git status --short

预期当前工作区可能已有很多未提交改动。实现者只能编辑本计划涉及的 CLI 终端、命令契约、测试和文档文件，不得还原或清理无关改动。

阅读相关文件：

    Get-Content -Raw ui\cli\prompt_input\terminal.py
    Get-Content -Raw ui\cli\prompt_input\session.py
    Get-Content -Raw ui\cli\commands.py
    Get-Content -Raw ui\cli\app.py
    Get-Content -Raw tests\test_cli_prompt_input_terminal.py
    Get-Content -Raw tests\test_cli_resume.py

实现低层 terminal surface 后，先运行终端输入相关测试：

    uv run python -m pytest tests/test_cli_prompt_input_terminal.py tests/test_cli_prompt_input_state.py -q

预期结果是所有测试通过。如果新增 `tests/test_cli_prompt_input_surface.py`，也加入命令：

    uv run python -m pytest tests/test_cli_prompt_input_surface.py -q

修正 `/resume` presentation 后运行：

    uv run python -m pytest tests/test_cli_resume.py tests/test_cli_commands.py -q

完成主要改动后运行编译检查：

    uv run python -m compileall ui services core

最后运行相关 CLI 测试集合：

    uv run python -m pytest tests/test_cli_prompt_input_terminal.py tests/test_cli_prompt_input_state.py tests/test_cli_pages.py tests/test_cli_resume.py tests/test_cli_commands.py tests/test_cli_connect.py tests/test_cli_mcp_trust_prompt.py -q

如果时间允许，再运行全量测试：

    uv run python -m pytest tests -q

手动验证需要真实交互式终端。在仓库根目录运行：

    uv run python -m ui.cli.app

在 CLI 中输入：

    /usage

预期：屏幕切换到用量页；按 `Esc` 后，终端恢复到输入 `/usage` 前的主屏幕状态，用量页内容不会留在 scrollback 中。

继续输入：

    /status

预期：状态页行为同上。按 `Esc` 后页面消失。

如果 `.onecode` 中有历史 session，输入：

    /resume

预期：session selector 在临时界面中显示。按 `Esc` 取消后 selector 消失。选择一个 session 后，恢复历史页也在临时界面中显示，按 `Esc` 后历史页消失，但 runtime 已切换到所选 session。

如果项目有 provider 配置可测试 `/connect`，输入：

    /connect

预期：provider selector、后续输入和完成反馈不在主屏幕留下大块临时 UI。若实现第一版尚未完成 shared scope，必须在本文 `Outcomes & Retrospective` 中说明 `/connect` 仍需后续收尾。

## Validation and Acceptance

验收标准一：所有 `presentation="page"` 的命令在 interactive TTY 下通过备用屏幕显示。用户进入 `/usage`、`/status`、`/memory`、`/permissions`、`/skills`、`/tasks`、`/mcp` 或 `/resume` 多匹配列表后，按 `Esc` 返回时，查看页内容不会留在终端滚动历史中。

验收标准二：`/resume [target]` 恢复成功后的历史展示是 page，而不是 inline。测试应证明 `dispatch_command(runtime, "/resume <target>").presentation == "page"`，并且主循环会通过 `show_page()` 展示恢复历史。

验收标准三：进入备用屏幕发生在首帧渲染之前。低层测试应读取 fake stdout，断言 `ENTER_ALT_SCREEN + CLEAR_AND_HOME` 的位置早于 page 标题、selector 标题或 confirm 文本。

验收标准四：退出备用屏幕发生在所有结束路径中。测试应覆盖正常选择、Esc 取消、Ctrl-C 或 KeyboardInterrupt 路径，断言输出包含 `EXIT_ALT_SCREEN` 和显示光标序列 `\x1b[?25h`。

验收标准五：非 TTY 行为不发送备用屏幕控制码。使用 `StringIO` 这类默认非 TTY stream 的测试不应出现 `\x1b[?1049h` 或 `\x1b[?1049l`。非 TTY 下 `show_page()` 仍应打印 renderable，保持脚本或测试可读。

验收标准六：普通主 prompt 不进入备用屏幕。`read_prompt()` 的测试应证明主 prompt 不输出 `\x1b[?1049h`，因为普通对话内容应该留在主屏幕历史中。

验收标准七：相关测试和编译检查通过：

    uv run python -m pytest tests/test_cli_prompt_input_terminal.py tests/test_cli_prompt_input_state.py tests/test_cli_pages.py tests/test_cli_resume.py tests/test_cli_commands.py tests/test_cli_connect.py tests/test_cli_mcp_trust_prompt.py -q
    uv run python -m compileall ui services core

如果全量测试可运行，也应通过：

    uv run python -m pytest tests -q

## Idempotence and Recovery

本计划的改动主要是 additive-first：先新增 terminal surface 能力和测试，再逐步切换 page/select/confirm 使用它。重复运行测试和手动验证不会修改用户文件，除了 CLI 正常运行可能写入 `.onecode` transcript、trace、history 或 provider 配置。手动测试 `/connect` 可能修改 `.env`，如果不希望改动 provider 配置，可以跳过 `/connect` 手动验证，只运行自动化测试。

如果备用屏幕实现后发现终端被卡在空白界面，通常是没有发送 `EXIT_ALT_SCREEN`。可以在当前终端输入或粘贴以下字符对应的控制序列恢复，或者关闭当前 terminal tab：

    printf '\033[?1049l\033[?25h'

在 PowerShell 中可运行：

    [Console]::Out.Write("`e[?1049l`e[?25h")

如果测试出现大量控制码污染，先检查是否把非 TTY 判断放在 surface 内部，确保 `StringIO` 不发送 alternate screen 序列。不要为了让测试通过删除备用屏幕逻辑；应让 fake TTY 测试显式 opt in。

如果 `/connect` shared scope 一次性实现风险太大，可以先提交 page/select/confirm 的备用屏幕改动，并在本文 `Progress` 中把 `/connect` 标为未完成。但最终完成本计划前，需要统一多步骤 flow，避免 selector 进入备用屏幕而后续 text/password 输入回到主屏幕。

## Artifacts and Notes

当前关键路径摘录：

    ui/cli/app.py::main_loop_async()
        if result.presentation == "page":
            await show_page(result.renderable)
        else:
            renderer.print_renderable(result.renderable)

当前 `_run_session()` 的核心结构：

    driver.hide_native_cursor()
    try:
        driver.render(state)
        while True:
            event = driver.read_event()
            result = apply_event(state, event)
            driver.render(state)
            ...
    finally:
        driver.show_native_cursor()

目标结构应变为：

    driver.hide_native_cursor()
    try:
        if surface == "alternate":
            driver.enter_alternate_screen()
        driver.render(state)
        while True:
            ...
    finally:
        if surface == "alternate":
            driver.exit_alternate_screen()
        driver.show_native_cursor()

备用屏幕控制码：

    ENTER_ALT_SCREEN = "\x1b[?1049h"
    EXIT_ALT_SCREEN = "\x1b[?1049l"
    CLEAR_AND_HOME = "\x1b[2J\x1b[H"

参考行为说明：进入 `/usage` 时主屏幕被终端保存，备用屏幕显示用量页；按 `Esc` 后 OneCode 发送 `EXIT_ALT_SCREEN`，终端丢弃用量页并恢复主屏幕。因此用户的 shell 历史和普通对话输出保持原样，临时查看页像从未出现过。

## Interfaces and Dependencies

本计划不引入第三方依赖，只使用终端 ANSI/DEC 控制序列和现有标准库代码。

如果选择在 `ui/cli/prompt_input/terminal.py` 中实现，最终应存在这些常量或等价私有常量：

    ENTER_ALT_SCREEN = "\x1b[?1049h"
    EXIT_ALT_SCREEN = "\x1b[?1049l"
    CLEAR_AND_HOME = "\x1b[2J\x1b[H"

`TerminalDriver` 应提供这些方法或通过组合对象提供等价能力：

    def enter_alternate_screen(self) -> None:
        """Enter alternate screen before the first transient frame."""

    def exit_alternate_screen(self) -> None:
        """Exit alternate screen if this driver entered it."""

这些方法必须使用 `self._write(...)` 和 `self._flush()`，并通过 `self.is_interactive` 或 `_is_tty(self.stdout)` 避免在非 TTY 下输出控制码。`enter_alternate_screen()` 应写入 `ENTER_ALT_SCREEN + CLEAR_AND_HOME`。`exit_alternate_screen()` 应写入 `EXIT_ALT_SCREEN`。

`ui/cli/prompt_input/session.py::_run_session()` 的签名应增加 surface 参数，建议：

    SessionSurface = Literal["inline", "alternate"]

    def _run_session(
        initial_state: PromptInputState,
        prompt: str,
        suggestion_provider: Callable[[PromptInputState], tuple[object, ...]] | None,
        *,
        surface: SessionSurface = "inline",
    ) -> object | None:
        ...

调用方建议如下：

    read_prompt(...) -> _run_session(..., surface="inline")
    read_text_sync(...) -> _run_session(..., surface="inline") unless inside transient scope
    read_confirm_sync(...) -> _run_session(..., surface="alternate")
    select_item(...) -> _run_session(..., surface="alternate")
    show_page(...) -> _run_session(..., surface="alternate")

如果实现 shared transient scope，应保证 nested calls 不重复写 `ENTER_ALT_SCREEN` 和 `EXIT_ALT_SCREEN`。可以用 `contextvars.ContextVar` 保存当前 active surface，也可以在 `session.py` 内使用一个简单的 thread-local 状态。由于 OneCode 的 prompt input session 通过 `asyncio.to_thread()` 执行同步终端读取，使用 thread-local 时要确保进入 scope 和实际 `_run_session()` 发生在同一线程；如果做不到，应优先使用显式传参或 context object，避免隐式状态跨线程失效。

`ui/cli/commands.py::_resume()` 和 `ui/cli/app.py::_resume_history_result()` 应让恢复历史 page 化：

    return CommandResult(
        runtime=resumed,
        renderable=renderer.render_group(...),
        presentation="page",
    )

不要把备用屏幕控制码写入 `renderer.py` 或具体 view。`renderer.py` 只负责把 runtime 状态转成 Rich renderable 或字符串；终端模式属于 `prompt_input` 层。

2026-06-12 / Codex: 初始计划创建。原因：用户要求基于 OneCode 当前 CLI 代码、`PLANS.md` 和参考实现思路，撰写中文 ExecPlan，用统一备用屏幕机制解决 `/resume`、`/usage`、`/status` 等临时界面退出后残留在终端历史中的问题。
