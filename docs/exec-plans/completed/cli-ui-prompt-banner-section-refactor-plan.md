# 重构 CLI 终端 UI：彩色吉祥物、统一标题横线视图、输入框横线与反色历史

本 ExecPlan 是一份"活文档"。`Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` 四个小节必须随实现进度持续更新。

本仓库根目录存在 `PLANS.md`，本文档必须按 `PLANS.md` 的要求维护（自包含、面向新手、以可观察行为为验收标准、living document）。

## Purpose / Big Picture

本次改动只影响 OneCode 的命令行界面（CLI）观感，不改变任何 agent 运行逻辑、工具、权限或上下文行为。完成后，用户在终端里会看到四个可见变化：

第一，启动时会出现一只彩色的字符画小猫作为吉祥物，排在产品名/工作区/模型信息的左侧（类似很多 CLI 启动横幅左边的彩色 logo）。

第二，用户输入提示符从 `onecode> ` 改为 `> `；当前正在编辑的输入框上下各有一条铺满终端宽度的横线，把输入区域框出来。

第三，用户每次提交输入后，提交过的那一行会以"反色"（终端把前景色和背景色对调，即 ANSI SGR 代码 `\x1b[7m`）显示，使历史用户输入与模型输出在视觉上明确区分；`/resume` 恢复出来的历史里，用户行同样用反色显示。

第四，模型（assistant）每次回复的开头会带上 `onecode> ` 前缀；`/status`、`/resume`、`/usage`、`/memory`、`/permissions`、`/skills`、`/tasks`、`/mcp` 等命令进入的页面，不再使用四边框（rich `Panel`），改为"仅顶部一条铺满终端宽度的横线 + 彩色标题 + 默认前景色正文"的样式，左右没有竖线、底部没有横线。

如何验证它在工作：在一个有 `.env`（含模型 provider 配置）的工作区里运行 `uv run python -m ui.cli.app`（或项目既有入口），观察启动横幅出现彩色小猫；输入一行文字回车，观察该行变成反色；运行 `/status`，观察页面只有顶部一条横线、标题有颜色、正文为默认色，左右与底部无边框。自动化层面，运行 `uv run python -m pytest tests -q` 应全部通过（含本计划新增/修改的测试）。

本次采用"彻底重构、不保留旧实现作为 fallback"的策略：渲染只保留一条路径（彩色），不再保留"去色版"渲染分支。唯一保留的非交互路径是 `read_batch_line`（用于管道/CI/非 TTY 环境读取纯文本输入），因为它服务的是"非交互式输入读取"这个独立场景，不是"旧 UI 的退路"。这一点已与需求方确认保留。

## Progress

- [x] (milestone 1) 主题与吉祥物：在 `ui/cli/theme.py` 新增彩色小猫字符画常量与 `onecode.mascot` 样式；改 `render_banner()` 为"小猫 + 信息"双列布局。
- [x] (milestone 1) 统一标题横线视图：在 `ui/cli/views/common.py` 新增 `titled_section()`（基于 rich `Rule`），并把所有 `Panel(...)` 调用点替换为它。
- [x] (milestone 2) 页面彩色管线：把 `render_to_text()` 改为彩色输出且用真实终端宽度；`show_page()` 传入宽度；新增 `strip_ansi()` 工具；更新依赖纯文本断言的测试。
- [x] (milestone 3) 输入与输出：用户提示符改 `> `；输入框上下加全宽横线；提交后该行反色重绘；`/resume` 恢复历史用户行反色；assistant 输出加 `onecode> ` 前缀。
- [x] 运行完整测试套件并修正所有回归。

进度记录：2026-06-12 23:xx 完成全部三个 milestone，`uv run python -m pytest tests -q` 共 399 项全部通过。

## Surprises & Discoveries

- Observation: 页面（`/status` 等）当前会丢失所有颜色。
  Evidence: `ui/cli/views/common.py` 的 `render_to_text()` 用 `color_system=None` 构造 `Console`，`show_page()`（`ui/cli/prompt_input/session.py`）调用它得到纯文本后再在 alternate screen 显示，因此彩色标题/反色无法显示。这是 milestone 2 必须改的根因。

- Observation: `Console(width=...)` 在本机被忽略，导出宽度回退为 80。
  Evidence: 仅设置 `width=120` 时 `console.width` 返回 80，导致 `render_banner` 的长工作区路径在双列网格里被截断（`test_banner_shows_only_product_workspace_and_model` 失败）。修复：`render_to_text()` 同时显式传入 `height=10_000`，rich 才会采用显式 `width`（rich 仅在 width 与 height 都显式设置时走 `ConsoleDimensions` 捷径，否则回退到检测到的终端宽度）。

（实现过程中继续补充。）

## Decision Log

- Decision: 渲染只保留彩色单路径，删除 `render_to_text()` 的去色实现；测试改用 `strip_ansi()` 去色后断言。
  Rationale: 需求方要求"能重构就重构，不保留老实现作为 fallback"。保留两套渲染会导致显示与测试不一致、且增加维护面。`strip_ansi()` 只是字符串工具，不是第二套渲染路径。
  Date/Author: 2026-06-12 / 计划作者。

- Decision: 保留 `read_batch_line` 非交互输入路径。
  Rationale: 它服务于非 TTY（管道/CI/测试）下读取纯文本输入，属于独立场景而非"旧 UI 退路"；横线/反色等终端控制只在交互态有意义。需求方已确认保留。
  Date/Author: 2026-06-12 / 计划作者。

- Decision: 吉祥物为彩色，统一用新增的 `onecode.mascot` 主题样式着色。
  Rationale: 需求方明确要求彩色吉祥物；集中到主题样式便于统一调色与测试。
  Date/Author: 2026-06-12 / 计划作者。

## Outcomes & Retrospective

- 实际达成：三个 milestone 全部实现并与 Purpose 对照一致。
  - Milestone 1：`ui/cli/theme.py` 新增 `MASCOT_CAT` 与 `onecode.mascot` 样式；`render_banner()` 改为"彩色小猫 + 信息"双列网格（无 Panel）；`ui/cli/views/common.py` 新增 `titled_section()` 并重写 `titled_panel()`/`empty_panel()`；所有视图（status/usage/resume/connect/permissions/mcp/memory/skills/tasks 与 renderer 的 tools/history/trace/compact）由 `Panel` 改为 `titled_section`，返回类型改为 `Group`，移除 `rich.panel.Panel` 导入。
  - Milestone 2：`render_to_text()` 改为彩色单路径（`force_terminal=True` + `truecolor` + `export_text(styles=True)`），并接受 `width` 参数；`show_page()` 用 `driver.terminal_size().columns` 传入真实宽度；新增 `strip_ansi()`；`tests/test_cli_resume.py`、`tests/test_cli_commands.py` 中的纯文本断言改为先 `strip_ansi`；同步更新 `docs/design-docs/cli-architecture.md` 措辞。
  - Milestone 3：提示符由 `onecode> ` 改为 `> `（交互与非交互 3 处）；`render_state_text()` 增加 `width` 参数并在 prompt 模式上下绘制 `width-1` 宽全宽横线；新增 `TerminalDriver.commit_prompt_line()` 反色重绘提交行，`_run_session()` 在 prompt 提交时调用；`_restored_message_line()` 的 user 行改 `reverse` 样式；`render_assistant()` 加 `onecode> ` 前缀，流式路径在首个 delta 前手动打印一次前缀（两路径恰好一次）。
- 遗留：未做真机手动观感验证（需要带 `.env` 的工作区交互运行）；自动化层面 `uv run python -m pytest tests -q` 399 项全绿。
- 经验教训：rich 的 `Console(width=...)` 只有在同时显式设置 `height` 时才生效，否则回退到检测宽度——这是页面/横幅宽度相关测试稳定性的关键。

## Context and Orientation

OneCode 是一个 Python 代码 agent 运行时。它的命令行界面全部位于 `ui/cli/` 目录下，与 `core/`、`services/` 等运行时逻辑分层隔离（参见 `AGENTS.md` 的依赖边界要求：UI 属于独立层，不应反向被核心依赖）。本计划只改 `ui/cli/` 下的文件和对应测试，以及一处设计文档措辞。

下面用平实语言定义本计划用到的术语，并指出它们在代码中的位置：

"渲染（render）函数"：返回一个 rich 库的"可渲染对象"（renderable，如 `Panel`、`Table`、`Text`、`Group`、`Rule`）的函数。CLI 通过 `ui/cli/renderer.py` 的 `console().print(...)`（即 `print_renderable`）把它们打印到终端。

"rich `Panel`"：rich 提供的"带四边框（上下左右都有线）的盒子"组件，可设标题和边框颜色。当前所有状态视图都用它。本计划要把它换掉。

"rich `Rule`"：rich 提供的"一条横线"组件，可在线上嵌入一个标题文字，可设对齐方式、线条字符与线条样式（颜色）。本计划用它实现"只有顶部一条铺满宽度的横线 + 彩色标题"。它的构造形如 `Rule(title, characters="─", style=<线条样式>, align="left")`，其中 `title` 可以是带样式的 `Text`，从而让标题有颜色而线条用另一种（较淡的）颜色。

"主题样式（theme style）"：在 `ui/cli/theme.py` 的 `RICH_THEME` 里定义的命名样式，例如 `onecode.title`（`bold cyan`）、`onecode.subtle`（`dim`）、`onecode.success`（`green`）等。渲染函数通过样式名引用颜色，便于统一调色。

"alternate screen（备用屏）"：终端的一种全屏临时画面，退出后恢复原来的滚动内容。`/status` 等命令的"页面（page）"就显示在备用屏里。相关代码在 `ui/cli/prompt_input/terminal.py`（`ENTER_ALT_SCREEN` / `EXIT_ALT_SCREEN`）与 `ui/cli/prompt_input/session.py` 的 `show_page()`。

"反色（reverse video）"：终端把字符的前景色与背景色对调显示的效果，ANSI 控制码是 `\x1b[7m` 开始、`\x1b[0m` 结束。代码里已用它实现输入光标（`ui/cli/prompt_input/terminal.py` 的 `_CURSOR_INVERSE_START`/`_CURSOR_INVERSE_END`）。rich 里对应的样式名是 `reverse`。

"ANSI 转义码"：终端用来控制颜色、光标移动、清屏的特殊字符序列，以 `\x1b[` 开头。"去除 ANSI（strip ansi）"指用正则把这些序列删掉，得到纯文本，便于测试断言。

关键现状文件与位置（实现者应先打开阅读）：

`ui/cli/theme.py`：定义 `SYMBOLS` 与 `RICH_THEME`。本计划在此新增小猫字符画常量与 `onecode.mascot` 样式。

`ui/cli/views/common.py`：共享视图辅助。含 `titled_panel()`、`empty_panel()`（当前基于 `Panel`）与 `render_to_text(renderable)`（当前去色、宽度写死 120）。本计划新增 `titled_section()`、`strip_ansi()`，并重写 `render_to_text()`。

`ui/cli/views/status.py`：`render_banner()`（启动横幅，当前是竖排三行文字的 `Panel`）、`render_status()`、`render_usage()`（均为 `Panel`）。

`ui/cli/views/resume.py`、`ui/cli/views/connect.py`、`ui/cli/views/permissions.py`、`ui/cli/views/mcp.py`、`ui/cli/views/memory.py`、`ui/cli/views/skills.py`、`ui/cli/views/tasks.py`：各状态视图，均直接构造 `Panel(...)`。

`ui/cli/renderer.py`：console 输出入口，并含 `render_tools()`、`render_history()`、`render_trace()`、`render_compact()`（均为 `Panel`），以及 `render_restored_messages()`/`_restored_message_line()`（恢复历史的逐行渲染，user 行当前是 `Text("> ...", style="onecode.info")`）、`render_assistant()`/`render_assistant_delta()`（assistant 输出文本）。

`ui/cli/prompt_input/session.py`：`read_prompt()` 把提示符设为 `"onecode> "`（用于交互态与非交互态 `read_batch_line` 两处）；`_run_session()` 是交互循环，提交时调用 `driver.finish_line()`；`show_page()` 把 renderable 经 `render_to_text()` 转文本后在备用屏显示。

`ui/cli/prompt_input/terminal.py`：`TerminalDriver` 负责把按键转事件并渲染；`render(state)` 调用模块级 `render_state_text(state, *, prompt)` 得到文本，再由 `_replace_rendered_text()` 做多行重绘；`terminal_size()` 返回终端尺寸（`.columns` 为宽度、`.lines` 为高度）。`render_state_text()` 的 `prompt` 分支当前只输出一行 `f"{prompt}{visible}"`，无横线。

`ui/cli/app.py`：`main_loop_async()` 是主循环，启动时 `print_renderable(render_banner(runtime))`；assistant 流式分支打印 `render_assistant_delta(event.text)`，无 delta 时打印 `render_assistant(final_text)`；命令分支按 `result.presentation == "page"` 调 `show_page()`。

相关测试：`tests/test_cli_prompt_input_terminal.py`（含 `render_state_text` 与会话渲染的断言）、`tests/test_cli_commands.py`、`tests/test_cli_resume.py`、`tests/test_cli_pages.py`（这些用到 `render_to_text` 或断言页面纯文本内容）。

## Plan of Work

整体分三个 milestone，每个都能独立验证。顺序是先做"纯加法/替换、风险低"的主题与视图风格统一，再做"页面彩色管线"（牵涉去色双路径的根因），最后做"输入框终端控制"（涉及全宽横线自动换行陷阱，风险最高）。

### Milestone 1：彩色吉祥物 + 统一"标题横线"视图风格

目标：启动横幅出现彩色小猫；所有状态视图从"四边框 Panel"变为"顶部横线 + 彩色标题 + 默认正文"。本 milestone 结束后，内联打印的视图（不经过页面去色）已经能看到新样式；页面里的颜色要等 milestone 2 才生效，但结构已经正确。

第一步，在 `ui/cli/theme.py` 新增小猫字符画与样式。在文件中新增一个模块级常量（放在 `SYMBOLS` 之后即可）：

    MASCOT_CAT = r"""
     /\_/\
    ( o.o )
     > ^ <
    """.strip("\n")

并在 `RICH_THEME` 字典里新增一项样式（颜色可后续微调）：

    "onecode.mascot": "bold yellow",

第二步，重写 `ui/cli/views/status.py` 的 `render_banner()`，改为"左侧彩色小猫 + 右侧信息"的双列布局，去掉 `Panel`。示例实现：

    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    from ui.cli.theme import MASCOT_CAT

    def render_banner(runtime: CliRuntime) -> Group:
        mascot = Text(MASCOT_CAT, style="onecode.mascot")
        info = Group(
            Text("OneCode", style="onecode.title"),
            Text(str(runtime.workspace), style="onecode.path"),
            Text(runtime.model, style="onecode.model"),
        )
        grid = Table.grid(padding=(0, 2))
        grid.add_column()
        grid.add_column()
        grid.add_row(mascot, info)
        return grid

注意：`render_banner` 的返回类型从 `Panel` 改为 rich 的可渲染对象（这里用 `Table.grid` 返回的 `Table`，或包成 `Group`）。`app.py` 里 `print_renderable(render_banner(runtime))` 无需改动，因为它接受任意 renderable。

第三步，在 `ui/cli/views/common.py` 新增统一的"标题横线"辅助函数 `titled_section()`，并用它重写 `titled_panel()` 与 `empty_panel()` 的内部实现（保留这两个函数名以减少调用方改动，但内部不再用 `Panel`）：

    from rich.console import Group
    from rich.rule import Rule
    from rich.text import Text

    def titled_section(title: str, body: object, *, style: str = "onecode.title") -> Group:
        heading = Rule(
            Text(f" {title} ", style=style),
            characters="─",
            style="onecode.subtle",
            align="left",
        )
        return Group(heading, body)

    def titled_panel(title: str, renderable: object, *, style: str = "onecode.info") -> Group:
        return titled_section(title, renderable, style=style)

    def empty_panel(title: str, message: str) -> Group:
        return titled_section(title, Text(f"{SYMBOLS.info} {message}", style="onecode.subtle"))

第四步，把所有直接构造 `Panel(...)` 的视图改用 `titled_section()`。逐个文件处理，规则是：原 `Panel(body, title=T, border_style=S, expand=False)` 改为 `titled_section(T, body, style=S)`；返回类型注解从 `Panel` 改为 `Group`（从 `rich.console` 导入 `Group`），并删除不再使用的 `from rich.panel import Panel`。涉及文件与函数：

- `ui/cli/views/status.py`：`render_status()`（标题 `Status`，原 `onecode.info`）、`render_usage()`（标题 `Usage`，原 `onecode.metric`）。
- `ui/cli/views/resume.py`：`render_session_summaries()`（标题 `Resume`，原 `onecode.info`）。
- `ui/cli/views/connect.py`：`render_connect_success()`（标题 `{SYMBOLS.success} Connected`，原 `onecode.success`）。
- `ui/cli/views/permissions.py`：`render_permissions()`（标题 `Permissions`，原 `onecode.permission`）。
- `ui/cli/views/mcp.py`：`render_mcp()` 的三个 `Panel` 出口（disabled、no servers、正常；标题均为 `MCP`，原 `onecode.info`）。
- `ui/cli/views/memory.py`：`render_memory()`（标题 `Memory`，原 `onecode.info`）。
- `ui/cli/views/skills.py`：`render_skills()` 的两个 `Panel` 出口（disabled、正常；标题 `Skills`，原 `onecode.info`）。
- `ui/cli/views/tasks.py`：`render_tasks()`（标题 `Tasks`，原 `onecode.info`）。
- `ui/cli/renderer.py`：`render_tools()`（标题 `Enabled tools`）、`render_history()`（标题 `Recent messages`）、`render_trace()`（标题 `Recent trace`）、`render_compact()`（标题 `Compacted session`，原 `onecode.success`）。这些函数的返回类型注解也从 `Panel` 改为 `Group`，并删除 `from rich.panel import Panel`（确认无其它使用后再删）。

第五步，确认 `renderer.py` 顶部对 `render_banner` 等的导入仍然有效（函数仍存在，只是返回类型变了）。

Milestone 1 验收（内联视图，先不经页面）：编写或运行一个最小脚本/测试，调用 `renderer.render_to_text(renderer.render_status(runtime))`（此时 milestone 2 还没改 `render_to_text`，仍是去色，但结构应已变化），断言输出里包含标题文字 `Status` 且不包含 `╭`、`│`、`╰`（`Panel` 的边框字符）。

### Milestone 2：页面彩色管线（去掉去色双路径）

目标：让 `/status`、`/resume` 等"页面"里能显示彩色标题与反色用户行。根因是 `render_to_text()` 去色且宽度写死。本 milestone 把它改成"彩色 + 真实终端宽度"的唯一渲染出口，并把依赖纯文本断言的测试改为先 `strip_ansi` 再断言。

第一步，在 `ui/cli/views/common.py` 重写 `render_to_text()` 并新增 `strip_ansi()`：

    import re

    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

    def strip_ansi(text: str) -> str:
        return _ANSI_RE.sub("", text)

    def render_to_text(renderable: object | None, *, width: int = 120) -> str:
        if renderable is None:
            return ""
        console = Console(
            record=True,
            force_terminal=True,
            color_system="truecolor",
            width=width,
            theme=RICH_THEME,
        )
        console.print(renderable)
        return console.export_text(styles=True).rstrip()

说明：`force_terminal=True` + `color_system="truecolor"` 让 rich 产出带颜色的输出；`export_text(styles=True)` 让导出的文本保留 ANSI 样式码（rich 会在每个样式片段结束处插入重置码 `\x1b[0m`，因此按 `\n` 切行做滚动不会破坏样式）。`width` 改为参数，默认 120 仅用于直接单元测试调用；运行时由 `show_page()` 传入真实宽度，保证顶部横线铺满当前终端。

第二步，修改 `ui/cli/prompt_input/session.py` 的 `show_page()`，先创建 driver、用其终端宽度渲染文本：

    async def show_page(renderable: object, *, title: str | None = None) -> None:
        driver = _driver_for_session("", "alternate")
        text = renderer.render_to_text(renderable, width=driver.terminal_size().columns)
        lines = tuple(text.splitlines() or ("",))
        state = PromptInputState(
            mode="page",
            page=PageState(
                lines=lines,
                title=title,
                height=driver.page_content_height(has_title=title is not None),
            ),
        )
        await asyncio.to_thread(
            _run_session,
            state,
            "",
            None,
            surface="alternate",
            driver=driver,
        )

第三步，更新依赖"纯文本"的测试，改为 `strip_ansi(...)` 后断言。具体：

- `tests/test_cli_resume.py`：三处 `renderer.render_to_text(result.renderable)` 之后的内容断言，改为对 `strip_ansi(renderer.render_to_text(...))` 断言（从 `ui.cli.views.common import strip_ansi`）。
- `tests/test_cli_commands.py`：`renderer.render_to_text(result.renderable)`、`renderer.render_to_text(renderer.render_banner(runtime))` 两处同样处理。
- `tests/test_cli_pages.py`：若其中对页面文本内容做精确/子串断言，统一改为先 `strip_ansi`。若它只断言 `Panel` 边框字符的存在性，则改为断言新样式（标题文字存在、无 `│`/`╰`）。
- 任何此前断言"输出不含 ANSI/不含颜色"的用例：删除该断言或改为"strip 后等于预期纯文本"。这体现"彻底重构、不保留去色路径"。

第四步，更新设计文档措辞：`docs/design-docs/cli-architecture.md` 第 141 行附近"测试需要无颜色文本时使用 `renderer.render_to_text(renderable)`"，改为"测试需要无颜色文本时使用 `strip_ansi(renderer.render_to_text(renderable))`；`render_to_text` 现在输出带 ANSI 样式的彩色文本"。

Milestone 2 验收：运行 `uv run python -m pytest tests/test_cli_commands.py tests/test_cli_resume.py tests/test_cli_pages.py -q`，应全部通过。手动运行 CLI 后执行 `/status`，应看到顶部横线、彩色标题、默认色正文，且无四边框。

### Milestone 3：输入提示符、输入框横线、提交反色、恢复历史反色、assistant 前缀

目标：交互态输入提示符为 `> `；当前输入框上下各一条全宽横线；回车提交后该行以反色重绘留在滚动区；`/resume` 恢复历史的用户行用反色；assistant 回复开头带 `onecode> `。

第一步，改用户提示符。`ui/cli/prompt_input/session.py` 的 `read_prompt()` 中把 `"onecode> "` 改为 `"> "`（包括 `_default_driver("onecode> ")` 判断、`read_batch_line("onecode> ")` 非交互分支、`_run_session(..., "onecode> ", ...)` 交互分支，共 3 处统一改为 `"> "`）。

第二步，给 `render_state_text()` 增加宽度并在 prompt 模式画上下全宽横线。`ui/cli/prompt_input/terminal.py`：

把签名改为 `def render_state_text(state, *, prompt="", width: int = 80) -> str:`。在 `prompt`/`text`/`password` 分支里，仅当 `state.mode == "prompt"`（真正的主输入框，而非 `text`/`password` 这类一次性输入）时，在输入行上下添加横线。横线宽度用 `max(1, width - 1)` 条 `─`，减 1 是为了避免横线正好等于终端列数时被终端自动折行、从而打乱 `_replace_rendered_text()` 的行数统计（这是本 milestone 最关键的陷阱）。示意：

    rule = "─" * max(1, width - 1)
    if state.mode == "prompt":
        lines = [rule, f"{prompt}{visible}"]
        if state.suggestions.active:
            for index, item in enumerate(state.suggestions.items[:8]):
                pointer = SYMBOLS.pointer if index == state.suggestions.selected else " "
                detail = f"  {item.description}" if item.description else ""
                lines.append(f"{pointer} {item.display}{detail}")
        lines.append(rule)
        return "\n".join(lines)
    # text/password 维持原来的单行渲染

并在 `TerminalDriver.render()` 把宽度传进去：

    def render(self, state: PromptInputState) -> None:
        text = render_state_text(state, prompt=self.prompt, width=self.terminal_size().columns)
        self._replace_rendered_text(text)

注意：`render_state_text` 的默认 `width=80` 仅服务于直接调用它的单元测试（如 `tests/test_cli_prompt_input_terminal.py` 里 `render_state_text(state, prompt="onecode> ")`）。这些既有断言（如 `rendered.startswith("onecode> ")`）会因为现在 prompt 模式前面多了一行横线而失败——需要更新：改为断言 `f"{prompt}{...}"` 出现在某一行中（例如 `assert any(line.startswith("onecode> ") for line in rendered.splitlines())`），并可新增断言"首行与末行是横线"。

第三步，提交后把输入行重绘为反色。`ui/cli/prompt_input/terminal.py` 新增一个方法，用已有的反色码把"提交后的单行"写出（替换掉当前多行输入框）：

    def commit_prompt_line(self, text: str) -> None:
        committed = f"{_CURSOR_INVERSE_START}{self.prompt}{text}{_CURSOR_INVERSE_END}"
        self._replace_rendered_text(committed)

`ui/cli/prompt_input/session.py` 的 `_run_session()` 在拿到 `result.submission` 且当前是主输入框（prompt 模式）时，先重绘反色行再换行：

    if result.submission is not None:
        if state.mode == "prompt":
            driver.commit_prompt_line(result.submission.text)
        driver.finish_line()
        return result.submission

这样历史里每条用户输入是一行反色 `> 文本`，而当前正在编辑的输入框仍是"上下横线 + `> 输入`"。`text`/`password` 等模式不走 submission 分支（它们走 `modal_result`），因此不受影响，保持原样。

第四步，恢复历史的用户行改反色。`ui/cli/renderer.py` 的 `_restored_message_line()` 中，user 分支：

    if role == "user":
        return Text(f"> {preview(message.get('content'))}", style="reverse")

`reverse` 是 rich 的反色样式；因为 milestone 2 已让页面保留 ANSI，`/resume` 页面里这行会显示为反色。

第五步，assistant 输出加 `onecode> ` 前缀。`ui/cli/app.py` 的 assistant 渲染分支：

- 流式：在收到第一个 `assistant_delta` 时先打印一次前缀，再打印增量。用一个布尔标志实现：

      saw_delta = False
      ...
      if event.type == "assistant_delta":
          if not saw_delta:
              print("onecode> ", end="", flush=True)
          saw_delta = True
          print(renderer.render_assistant_delta(event.text), end="", flush=True)

- 非流式：把最终文本前缀化，改 `print(renderer.render_assistant(final_text))` 处，让 `render_assistant` 的返回值带前缀，或在打印处拼接 `print(f"onecode> {renderer.render_assistant(final_text)}")`。建议在 `ui/cli/renderer.py` 的 `render_assistant()` 内统一加前缀，避免两处重复：

      def render_assistant(text: str) -> str:
          body = text if text else "(assistant returned no text)"
          return f"onecode> {body}"

  若选择在 `render_assistant` 内加前缀，则流式分支的"先打印一次前缀"逻辑要保持只在流式时手工加（因为流式不经过 `render_assistant`）。确保两条路径都"恰好一次"前缀。

Milestone 3 验收：手动运行 CLI，提示符为 `> `，输入框上下有铺满宽度的横线；输入一行回车，该行变反色；assistant 回复以 `onecode> ` 开头；运行 `/resume` 选择一个会话，恢复出的用户行为反色。自动化：运行 `uv run python -m pytest tests/test_cli_prompt_input_terminal.py -q` 通过（含更新后的断言）。

## Concrete Steps

所有命令在仓库根目录 `D:/study/OneCode`（Windows PowerShell）下运行。先激活虚拟环境（首次需 `uv sync --dev`）：

    .\.venv\Scripts\Activate.ps1

每个 milestone 完成后运行编译检查与相关测试：

    uv run python -m compileall ui
    uv run python -m pytest tests/test_cli_prompt_input_terminal.py tests/test_cli_commands.py tests/test_cli_resume.py tests/test_cli_pages.py -q

全部完成后运行完整套件：

    uv run python -m pytest tests -q

手动观察 CLI（需要工作区内有配置好的 `.env`）：

    uv run python -m ui.cli.app

预期启动横幅形如（颜色无法在此纯文本展示，实际为彩色小猫与青色标题）：

     /\_/\    OneCode
    ( o.o )   D:\study\OneCode
     > ^ <    <model-name>

输入并回车后，历史行形如（实际为反色）：

    > 你好

assistant 回复形如：

    onecode> 你好，我可以帮你……

运行 `/status` 后页面顶部形如（顶部一条横线带彩色标题，下面是默认色键值表，无左右竖线与底部横线）：

    ── Status ───────────────────────────────────────────────
    workspace   D:\study\OneCode
    session     <id>
    ...

## Validation and Acceptance

验收以可观察行为为准：

启动横幅出现彩色字符画小猫，且产品名/工作区/模型信息排在其右侧。

交互输入提示符为 `> `；当前输入框上下各有一条铺满终端宽度的横线；用 `─` 字符且不会因宽度问题发生异常折行（连续快速输入、退格、左右移动光标时输入框不串行、不残留）。

回车提交后，刚提交的那一行变为反色显示并留在滚动区；新的输入框重新出现在下方。

模型回复以 `onecode> ` 开头（流式与非流式都恰好一次前缀）。

`/status`、`/resume`、`/usage`、`/memory`、`/permissions`、`/skills`、`/tasks`、`/mcp` 页面均为"顶部一条全宽横线 + 彩色标题 + 默认色正文"，无四边框；`/resume` 恢复历史中的用户行为反色。

测试层面：`uv run python -m pytest tests -q` 全绿。新增/修改的断言能"改前失败、改后通过"——具体地，`tests/test_cli_prompt_input_terminal.py` 中关于 prompt 模式渲染的断言在加横线后必须更新；关于页面/命令文本的断言在 `render_to_text` 改彩色后必须经 `strip_ansi`。

## Idempotence and Recovery

所有步骤都是对源码的确定性编辑，可重复运行：重复执行编辑不会产生额外副作用；重复运行测试与 CLI 不会损坏数据（CLI 仅读写 `.onecode/` 下的会话与历史文件，本计划不改这些写入逻辑）。

若某个 milestone 引入回归：milestone 之间相互独立，可单独回退某一文件的改动而不影响其它 milestone。最高风险点是 milestone 3 的全宽横线折行问题——若出现输入框重绘错乱，先把横线宽度从 `width - 1` 进一步减小（如 `width - 2`）或临时去掉横线以隔离问题，再排查 `_replace_rendered_text()` 的行数统计是否与实际终端折行一致。

清理：本计划不产生需要清理的临时文件；完成后此 ExecPlan 应从 `docs/exec-plans/active/` 移动到 `docs/exec-plans/completed/`。

## Artifacts and Notes

吉祥物字符画（最终采用的紧凑三行版，置于 `ui/cli/theme.py`）：

     /\_/\
    ( o.o )
     > ^ <

`Rule` 用法要点：标题用带样式的 `Text` 着色，线条用 `style="onecode.subtle"` 保持较淡，`align="left"` 让标题靠左，`characters="─"` 指定线条字符；`Rule` 默认就会铺满当前 console 宽度，因此页面里它会随 `render_to_text(width=...)` 传入的真实终端宽度铺满。

`render_to_text` 颜色保留要点：`export_text(styles=True)` 会保留 ANSI；rich 在每个样式片段末尾插入 `\x1b[0m`，所以按 `\n` 切片做分页滚动不会把样式码截断到跨行。

## Interfaces and Dependencies

只依赖已在用的 rich 库（`rich.console.Group`、`rich.rule.Rule`、`rich.table.Table`、`rich.text.Text`、`rich.console.Console`），不引入新依赖。

本计划结束后必须存在以下接口：

在 `ui/cli/theme.py`：

    MASCOT_CAT: str            # 多行字符画字符串
    # RICH_THEME 含新键 "onecode.mascot"

在 `ui/cli/views/common.py`：

    def titled_section(title: str, body: object, *, style: str = "onecode.title") -> Group: ...
    def strip_ansi(text: str) -> str: ...
    def render_to_text(renderable: object | None, *, width: int = 120) -> str: ...   # 现在输出带 ANSI 的彩色文本
    # titled_panel / empty_panel 保留同名，内部改用 titled_section，返回 Group

在 `ui/cli/views/status.py`：

    def render_banner(runtime: CliRuntime) -> Group | Table: ...   # 小猫 + 信息双列，无 Panel

在 `ui/cli/prompt_input/terminal.py`：

    def render_state_text(state: PromptInputState, *, prompt: str = "", width: int = 80) -> str: ...
    # TerminalDriver.render() 传入 width=self.terminal_size().columns
    # TerminalDriver.commit_prompt_line(self, text: str) -> None   # 反色重绘提交行

在 `ui/cli/prompt_input/session.py`：

    # read_prompt 提示符为 "> "
    # _run_session 在 prompt 模式提交时调用 driver.commit_prompt_line(...)
    # show_page 用 driver.terminal_size().columns 作为 render_to_text 的 width

在 `ui/cli/renderer.py`：

    def render_assistant(text: str) -> str: ...   # 返回值带 "onecode> " 前缀
    # _restored_message_line 的 user 分支样式改为 "reverse"
    # render_tools/render_history/render_trace/render_compact 返回 Group（基于 titled_section）

所有视图模块（resume/connect/permissions/mcp/memory/skills/tasks）的渲染函数返回类型从 `Panel` 改为 `Group`，并移除对 `rich.panel.Panel` 的导入。

---

变更记录：本文件为初版，依据需求方三轮澄清（输入提示符与 AI 前缀、字符画彩色吉祥物、恢复历史反色、彻底重构不留 fallback、保留非交互 `read_batch_line`）整理而成。后续实现中如有设计调整，须在 `Decision Log` 记录原因并同步更新各小节。
