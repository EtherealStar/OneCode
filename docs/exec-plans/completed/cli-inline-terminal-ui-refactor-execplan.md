# 用内联终端渲染（prompt_toolkit + Rich）替换 Textual 全屏 CLI，实现 Claude Code 风格交互

本 ExecPlan 是一份活文档。`Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` 四个章节必须随工作推进持续更新。

本仓库的 ExecPlan 规范见仓库根目录 `PLANS.md`，本文档必须按 `PLANS.md` 的要求维护（自包含、面向新手、以可观察行为验收、记录决策与发现）。

**本文档是一份独立计划，不更新、不延续 `docs/exec-plans/active/textual-cli-ui-refactor-execplan.md`。** 那次 Textual 全屏重写将被本计划整体取代并回滚其 UI 外壳；`core/`、`services/`、`infrastructure/` 边界保持不变。

## Purpose / Big Picture

今天的 OneCode CLI 使用 Textual 全屏应用：自绘深色背景、运行在备用屏幕里、对话历史存在于 App 内部控件而非终端 scrollback。用户退出后看不到之前的对话，界面也不像「真正的终端」。

本计划把 TTY 路径改为 **内联终端渲染模型**（与 Claude Code / Ink 的 Static + dynamic 分层同类）：定稿内容打印进终端正常缓冲区（继承终端白/黑背景、可滚动回看）；底部输入框、流式预览、斜杠补全画在可擦除的动态区；`/resume`、`/status` 等临时界面进入备用屏幕，按 `Esc` 退出后主屏幕恢复且临时内容不进入 scrollback。

完成后用户运行 `uv run python -m ui.cli.app` 应看到：

- 终端背景色与宿主一致（白终端白底、黑终端黑底），不强制 GitHub 深色主题。
- 启动横幅显示 `OneCode`、模型名、cwd（可保留吉祥物字符画）。
- 历史用户输入以 **反色 `>`** 行展示；AI 每条回复以 **`onecode>`** 开头；当前输入框为 **`>` + 上下横线边框**（参考 Claude Code 图 1、图 4）。
- 输入 `/` 出现命令补全列表（命令名 + 描述列）；↑↓ 选择；**回车执行**选中命令；**Tab 只补全到输入框不执行**。
- Agent 运行时可继续输入，消息进入 **队列**，当前轮结束后按序执行（参考 Claude Code 排队行为）。
- 流式回复在动态区 **实时 Markdown 渲染**（带节流），结束后定稿进 scrollback。
- `/status`、`/resume` 等打开全屏临时界面（图 2、图 3 类体验）；`Esc` 返回后主对话区不变、临时界面不留历史。
- 非 TTY（管道/CI）仍走 `batch.py`，不依赖 prompt_toolkit 或 Textual。

如何验证：在真实终端（Windows Terminal / iTerm / 系统终端）运行 `uv run python -m ui.cli.app`，切换浅色/深色配置文件各测一次；完成一轮对话后滚动终端应能看到历史；`/status` 后 `Esc` 不应在 scrollback 留下状态页内容。

## Progress

- [x] (2026-06-13) 里程碑 0：依赖切换与 spike 完成；`prompt_toolkit` 内联输入、动态区擦除、终端背景探测、反色用户行和 live Markdown 技术路径可行。
- [x] (2026-06-13) 里程碑 1：删除 `ui/cli/tui/` 与 Textual 依赖；建立 `ui/cli/terminal/` 骨架；TTY 入口改为 `InlineRepl`，非 TTY 仍走 `batch.py`。
- [x] (2026-06-13) 里程碑 2：静态区消息渲染完成；用户输入用反色 `>`，assistant 定稿以 `onecode>` + Markdown 输出，工具生命周期和结果摘要写入 scrollback。
- [x] (2026-06-13) 里程碑 3：动态输入框和补全完成；`/` 与 `@` 补全接入 `suggestions_for()`，Enter 采纳并提交，Tab 只补全，运行中输入进入 FIFO 队列。
- [x] (2026-06-13) 里程碑 4：流式运行态完成；动态区 live Markdown 预览、工具状态行、Esc 取消和完成后静态区定稿已落地。
- [x] (2026-06-13) 里程碑 5：临时界面完成；`/status` page、`/resume` selector、`/connect` flow、TTY permission prompt、MCP trust prompt 均走 `terminal/` 内联/备用屏幕实现。
- [x] (2026-06-13) 里程碑 6：测试与文档收口完成；`uv run python -m compileall ui\cli` 通过；`uv run python -m pytest tests -q` 为 `403 passed`；`rg -n "ui/cli/tui|textual|DeferredTextual|OneCodeApp|LoadingScreen|TrustScreen" ui\cli tests pyproject.toml` 无代码命中；本计划已移至 `docs/exec-plans/completed/`。

## Surprises & Discoveries

- Observation: `prompt_toolkit.Application(full_screen=False, erase_when_done=True)` 足以支撑内联动态区，备用屏幕只需要用于 page、selector、permission 和 connect 这类临时整屏表面。
  Evidence: `tests/test_cli_terminal.py` 覆盖 prompt 输入、Enter/Tab 语义、streaming preview、selector、page 和 permission response；全量测试 `403 passed`。

- Observation: Textual 代码残留可以清到只剩历史文档，不需要保留兼容外壳。
  Evidence: `rg -n "ui/cli/tui|textual|DeferredTextual|OneCodeApp|LoadingScreen|TrustScreen" ui\cli tests pyproject.toml` 无命中；`pyproject.toml` 仅保留 `prompt-toolkit` 与 `rich`。

- Observation: live Markdown 预览必须对长文本和未闭合代码块做保护，否则动态区可能变高或渲染出误导性的补全 fence。
  Evidence: `ui/cli/terminal/stream_session.py` 对 unbalanced code fence 回退纯文本，并把预览限制到 `_PREVIEW_MAX_LINES`；对应测试覆盖 partial code fence 与 bounded height。

## Decision Log

- Decision: 放弃 Textual 全屏模型，TTY 路径改用 `prompt_toolkit` + Rich 内联渲染；**彻底删除** `ui/cli/tui/` 及 `textual`/`textual-dev` 依赖，不保留并行实现。
  Rationale: 用户要求 UI「如同就是终端」、背景随终端明暗、临时界面 Esc 后不留 scrollback；Textual 全屏自绘背景且历史不在真实 scrollback，与目标根本冲突。用户明确要求新计划且若要移除 Textual 就彻底移除。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: 定稿对话与工具结果走 **静态区**（Rich `Console(file=sys.stdout)` 直接 print，不设 background style）；输入、流式预览、内联补全走 **动态区**（`prompt_toolkit.Application(full_screen=False, erase_when_done=True)`）。
  Rationale: 复刻 Ink 的 Static/dynamic 分层；动态区退出时擦除自身，避免污染 scrollback。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: `/status`、`/resume` 选择器、权限确认、MCP trust、`/connect` 多步向导走 **备用屏幕（DEC 1049）** 临时表面，行为对齐已完成计划 `docs/exec-plans/completed/cli-transient-alternate-screen-plan.md` 的契约：进入发生在首帧前，退出在 `finally` 恢复，内容不进入主 scrollback。
  Rationale: 全屏列表/分页查看需要整屏布局；备用屏幕是成熟、可测的「退出即消失」机制；与主 REPL 内联区不冲突。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: 终端明暗主题 **自动探测**（优先 OSC 11 查询背景色，失败回退 `COLORFGBG`，再回退暗色）；Rich 主题只定义前景样式，**永不设置 background**。
  Rationale: 用户选择 auto_detect；内联模型下背景由终端宿主提供。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: Agent 运行中允许 **排队输入**（至少支持文本行队列；不在本计划范围实现 Claude Code 全部 queued command 变体）。
  Rationale: 用户选择 queue 而非锁住输入。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: `/status` 等状态命令 **简单版**：在临时 page 中渲染现有 `ui/cli/views/` + `renderer.render_*` 输出，不做图 3 多标签页。
  Rationale: 用户选择 simple；复用现有 Rich 视图资产。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: 流式阶段使用 **live Markdown**（动态区节流重绘），完成后将最终 Markdown 再打印进静态区一次。
  Rationale: 用户选择 live_md；需注意未完成代码块时的渲染稳定性，里程碑 0 spike 必须验证。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: 斜杠补全语义：**菜单打开时 Enter = 采纳并执行**；**Tab = 仅将选中项写入输入框不提交**；↑↓ 在 `suggestions_for()` 结果中移动。
  Rationale: 对齐 Claude Code 图 4 与用户明确需求。
  Date/Author: 2026-06-13 / 用户与规划 agent。

- Decision: 入口命令保持 `python -m ui.cli.app`；`build_runtime()` 签名与 batch 路径行为保持兼容（可选 `trust_prompt`、`permission_prompter`、`mcp_trust_mode`）。
  Rationale: 与仓库惯例一致；运行时层零改动。
  Date/Author: 2026-06-13 / 用户与规划 agent。

## Outcomes & Retrospective

已完成。TTY 路径现在是内联终端 REPL：静态区进入真实 scrollback，动态区用 `prompt_toolkit` 擦除式输入和流式预览，临时整屏交互用备用屏幕。Textual 外壳、依赖和测试桩已移除；非 TTY batch 路径保持独立；CLI 架构文档和消息渲染文档已更新为当前实现。

最终验证结果：

    uv run python -m compileall ui\cli
    # 通过

    uv run python -m pytest tests -q
    # 403 passed

    rg -n "ui/cli/tui|textual|DeferredTextual|OneCodeApp|LoadingScreen|TrustScreen" ui\cli tests pyproject.toml
    # 无输出

没有在自动化中验证真实浅色/深色宿主终端的肉眼效果；该项仍属于手动终验，运行 `uv run python -m ui.cli.app` 后切换终端主题即可观察背景继承、scrollback 和临时页退出行为。

## Context and Orientation

### 项目与边界

OneCode 是 Python 代码 agent 运行时。`core/` 主循环通过 `core/stream_events.py` 的 `AgentEvent` 向 UI 推送事件；`services/` 提供工具、权限、MCP、记忆等；`ui/cli/` 只是边界层：装配、输入、命令、渲染、用户确认，**不承载 agent 逻辑**。

架构总览见仓库根 `architecture.md`。CLI 设计见 `docs/design-docs/cli-architecture.md`（本计划完成后须改写为内联模型描述）。

### 关键术语

- **scrollback（滚动历史）**：终端主屏幕缓冲区里向上滚动仍可看到的内容。内联 `print` 会进入 scrollback；备用屏幕里的内容在退出后丢弃。
- **静态区**：只打印一次、不再重绘的定稿输出（横幅、用户行、AI 回复、工具结果摘要）。
- **动态区**：每帧或节流重绘的底部区域（输入框、补全菜单、流式预览）；由 prompt_toolkit 管理，`erase_when_done=True` 在阶段结束时擦掉。
- **备用屏幕（alternate screen）**：终端 DEC 模式 1049（`\x1b[?1049h` 进入、`\x1b[?1049l` 退出）。用于全屏临时界面，退出恢复进入前的主屏幕。
- **`CliRuntime`**：`ui/cli/types.py` 聚合一次会话的运行时组件（`loop`、`message_store`、`permission_prompter` 等）。
- **`CommandResult`**：`dispatch_command()` 返回值；`presentation` 为 `"inline"` 或 `"page"`；`interaction` 可为 `"resume_selector"` 或 `"connect"`。
- **`AgentEvent`**：事件 `type` 包括 `assistant_delta`、`tool_call_ready`、`tool_started`、`tool_progress`、`tool_result`、`completed`、`error` 等；字段以 `core/stream_events.py` 为准。

### 当前代码事实（2026-06-13）

TTY 路径在 `ui/cli/app.py::main()` 中构建 `CliRuntime` 后启动 `ui/cli/tui/app.py::OneCodeApp`（Textual）。`ui/cli/tui/theme.tcss` 写死 `#000000` 背景。用户行当前渲染为 `you> {line}`（`OneCodeApp.on_prompt_submitted`）。

可复用资产（**不改业务行为**）：

| 路径 | 用途 |
|:---|:---|
| `ui/cli/renderer.py` | Rich `render_*` 工厂、`print_renderable`（batch） |
| `ui/cli/views/` | status、usage、mcp 等视图 |
| `ui/cli/tool_renderers.py` | 工具结果一行摘要 |
| `ui/cli/commands.py` | `dispatch_command`、`visible_commands` |
| `ui/cli/suggestions.py` | `suggestions_for()` → `SuggestionItem` |
| `ui/cli/resume.py` | 会话列表与恢复 |
| `ui/cli/connect.py` | `write_provider_env` |
| `ui/cli/permissions.py` | `render_permission_panel` |
| `ui/cli/theme.py` | `RICH_THEME`、`SYMBOLS`、`MASCOT_CAT` |
| `ui/cli/batch.py` | 非 TTY 路径 |
| `ui/cli/input.py` | `read_batch_line`、`read_confirm_sync` |
| `ui/cli/types.py` | `CliRuntime`、`CommandResult` |

将删除：

| 路径 | 原因 |
|:---|:---|
| `ui/cli/tui/` 整个目录 | Textual 外壳 |
| `pyproject.toml` 中 `textual`、`textual-dev` | 彻底移除 Textual |
| `tests/test_cli_tui.py` 及测试中 Textual 专用桩 | 随外壳删除重写 |

参考材料（只读，不拷贝业务代码）：

- `docs/references/ui/index.md`：Claude Code Ink UI 机制索引。
- `docs/references/ui/components/PromptInput/index.md`：补全/提交/按键冲突处理。
- `docs/exec-plans/completed/cli-transient-alternate-screen-plan.md`：备用屏幕临时界面契约。

## Plan of Work

总体顺序：里程碑 0 验证关键技术 → 里程碑 1 删 Textual 并搭骨架 → 2–4 接通主对话 UX → 5 临时界面与权限 → 6 测试与文档。每步保持 `batch` 路径可运行。

### 依赖变更（里程碑 0/1）

编辑 `pyproject.toml`：

- 添加 `prompt-toolkit>=3.0.0`（与 Rich 兼容的 3.x）。
- 移除 `textual>=8.2.7` 与 dev 组 `textual-dev>=1.7.0`。
- 运行 `uv lock` 与 `uv sync --dev`。

验收：`uv run python -c "import prompt_toolkit; print(prompt_toolkit.__version__)"` 成功；`uv run python -c "import textual"` 失败（模块不存在）。

### 目标目录结构（重构后 `ui/cli/`）

    ui/cli/
      app.py                 # 改写 main()：TTY → InlineRepl；非 TTY → batch
      batch.py               # 保留
      input.py               # 保留；可能扩展 sync 确认 helper
      commands.py            # 复用
      suggestions.py         # 复用
      resume.py              # 复用
      connect.py             # 复用 write_provider_env；交互迁到 terminal/
      renderer.py            # 复用；新增静态区打印 helper
      tool_renderers.py      # 复用
      theme.py               # 扩展：light/dark Rich 主题 + 反色用户行样式
      types.py               # 复用
      views/                 # 复用
      permissions.py         # 保留 render_permission_panel；Prompter 改 terminal 实现
      terminal/              # 新增：内联 REPL 外壳
        __init__.py
        detect.py            # 终端背景探测、主题选择
        static_output.py     # 静态区：横幅、反色用户行、onecode> 前缀、工具块
        transient.py         # DEC 1049 生命周期、transient_terminal_scope
        completer.py         # suggestions_for → prompt_toolkit Completer + 元数据列
        prompt_session.py    # 边框输入框 Application、Tab/Enter 绑定
        stream_session.py    # 流式 live Markdown 动态区、Esc 取消
        queue.py             # 运行中输入队列
        page.py              # 备用屏幕分页/滚动查看 renderable
        selector.py          # 备用屏幕列表选择（/resume）
        connect_flow.py      # /connect 多步向导（备用屏幕）
        permission_prompt.py # 权限确认（备用屏幕或内联 modal）
        trust_prompt.py      # MCP trust 确认
        repl.py              # InlineRepl 主循环：装配、dispatch、run_agent、shutdown
        _spike.py            # 里程碑 0 spike（完成后可删或保留为开发入口）

### 里程碑 0：Spike（技术验证）

在 `ui/cli/terminal/_spike.py` 实现最小验证（不接入 agent）：

1. `detect.py`：实现 `detect_terminal_brightness() -> Literal["light","dark"]`（OSC 11 → COLORFGBG → dark）。
2. `static_output.py`：打印一行反色 `> /status`（Rich `reverse` 或 `black on white` / `white on black` 随主题切换）。
3. `prompt_session.py`：最小 `Application`，显示上下 `─` 横线与 `> ` 提示；`erase_when_done=True`；退出后 scrollback 只剩静态区测试行。
4. `stream_session.py`：模拟每 50ms 追加 Markdown 片段，`invalidate` 重绘；验证未完成代码块不崩溃。

运行：

    uv run python -m ui.cli.terminal._spike

验收：浅色/深色终端各运行一次，反色用户行可读；输入框有上下边框；退出 spike 后输入区消失；流式 spike 有 Markdown 样式。

### 里程碑 1：移除 Textual + REPL 骨架

1. 删除 `ui/cli/tui/` 全部文件。
2. 改写 `ui/cli/app.py::main()`：
   - stdin 非 TTY → `batch.run_batch(workspace)`（不变）。
   - TTY → `build_runtime(...)` 后 `InlineRepl(runtime).run()`（同步入口内部 `asyncio.run` 或等价）。
   - 移除对 `OneCodeApp`、`DeferredTextualPermissionPrompter` 的引用。
3. 实现 `terminal/repl.py::InlineRepl`：
   - 打印 `renderer.render_banner(runtime)` 到静态区。
   - 循环：等待输入 → 区分 slash / 普通消息 → `dispatch` 或 `run_agent`。
   - 首轮可先 echo 用户输入到静态区（反色 `>`）以验证循环。
4. `terminal/detect.py` + `theme.py`：根据亮度选择 `RICH_THEME_LIGHT` / `RICH_THEME_DARK`（从现有 `RICH_THEME` 拆分，去掉任何 background）。

验收：`uv run python -m ui.cli.app` 启动无 Textual；`rg textual ui/cli` 无匹配；`rg "ui/cli/tui" .` 无匹配（测试除外待里程碑 6 清理）。

### 里程碑 2：静态区消息渲染

扩展 `static_output.py`：

- `print_user_submitted(line: str)`：反色 `> {line}`（全宽反色或行内反色与 Claude Code 一致，里程碑 0 截图对比后固定一种）。
- `print_assistant_start()`：打印 `onecode>` 前缀（不换行则与首段 Markdown 同行；若 Rich Markdown 需块级，则前缀单独一行后立即接内容——实现时选「前缀与正文首行同行」优先）。
- `print_assistant_markdown(text: str)`：定稿 Markdown 块。
- `print_tool_banner(...)` / `print_tool_result(...)`：复用 `tool_renderers` 与现有 `ToolBanner` 逻辑的 Rich 表意（可从 `tui/widgets/tool_banner.py` **迁移算法**到 `terminal/static_output.py` 或 `ui/cli/tool_renderers.py`，**不保留** Textual 依赖）。

验收：手动输入一句话（可先 mock `loop.stream`）后，scrollback 中用户行反色、助手行以 `onecode>` 开头、工具摘要在静态区。

### 里程碑 3：输入框与补全

实现 `prompt_session.py` + `completer.py`：

- 布局：顶线 `─`、中间 `> ` + 输入、底线 `─`；底栏提示 `? for shortcuts · @ for agents`（文案可配置）。
- 集成 `suggestions_for(runtime, text, cursor)`：`/` 触发命令补全；`@` 触发文件补全；描述列显示 `SuggestionItem.description`。
- 按键绑定（菜单可见时）：
  - ↑/↓：移动选中项（自定义 `Completer` 或 `CompleteEvent` 状态）。
  - Enter：若有选中项 → 写入 `replacement` 并 **立即作为提交行** 交给 `repl`（执行命令或发消息）；若无菜单 → 提交当前文本。
  - Tab：若有选中项 → 仅 `replacement` 写入输入框，**不提交**；若无 → 默认补全行为。
- Agent 运行中：`repl` 不阻塞输入；新行入 `queue.py`；动态区显示队列条数（可选一行 `N queued`）。

验收：输入 `/` 见列表；↑↓ 高亮；Tab 只填框；Enter 执行 `/status` 进入临时页（里程碑 5 前可先 print 占位）；运行中输入第二句入队。

### 里程碑 4：流式运行与取消

实现 `stream_session.py` 与 `repl.run_agent`：

- `async for event in runtime.loop.stream(...)` 消费事件（字段与现 `OneCodeApp.run_agent` 一致）。
- `assistant_delta`：追加 buffer；每 50ms 节流调用 live Markdown 渲染到动态区；行首带 `onecode>`。
- `tool_*` 事件：工具横幅与结果写入 **静态区**（运行中即可见），动态区可显示「tool: name」状态行。
- `completed` / 流结束：将 buffer 定稿 `print_assistant_markdown` 到静态区；`erase` 动态区。
- Esc：`stream_session` 绑定中断 → 设置 cancel flag → 静态区打印 `已取消`（`onecode.warning`）；处理权限与 shutdown 与现逻辑一致。
- 当前轮 `finally`：从 `queue.py` 取下一条继续（若存在）。

验收：真实模型一轮对话有 live Markdown；工具调用见横幅；Esc 可取消；队列在上一轮结束后自动执行下一条。

### 里程碑 5：临时界面与信任/权限

实现 `transient.py`、`page.py`、`selector.py`、`connect_flow.py`、`permission_prompt.py`、`trust_prompt.py`：

- **契约**（继承已完成 transient 计划）：
  - `enter_alternate_screen()` 在首帧渲染前；`exit_alternate_screen()` 在 `finally`。
  - 渲染目标为当前 stdout（宿主 PTY）；不因 `stdout.isatty()==False` 把 page inline 打印到 scrollback。
  - 找不到可写终端时返回明确错误。
- `presentation="page"`：`page.show(renderable)` 全屏滚动，`Esc` 退出，**不向静态区写入 page 内容**。
- `interaction="resume_selector"`：`selector.run(items)`，选中后 `restore_runtime_from_target`；恢复摘要可选 inline 一行到静态区（如 `Resumed session …`），**不**把整段历史 page 到 scrollback（与 Claude Code 一致：历史在 session 内，不在终端留副本）。若产品需展示恢复的消息列表，仅在备用屏幕内浏览，Esc 后消失。
- `interaction="connect"`：`connect_flow.run(runtime)` 写 `.env` 并 `runtime.with_model_config()`。
- `CliPermissionPrompter`：在 `terminal/permission_prompt.py` 用备用屏幕或阻塞式 prompt_toolkit 对话框 `await request_permission`。
- MCP trust：`build_runtime(..., trust_prompt=...)` 注入 `trust_prompt.py` 回调；TTY 启动期可 `mcp_trust_mode="prompt"`（不再默认 skip）。

`/status`：调用 `renderer.render_status(runtime)` 在 page 中显示，无多标签。

验收：`/status` Esc 后 scrollback 无状态页；`/resume` 选择后 Esc 无列表残留；权限与 trust 在 TTY 内完成；`/connect` 更新模型后横幅区 model 更新。

### 里程碑 6：测试、文档与清理

1. 删除 `tests/test_cli_tui.py`；更新 `tests/test_async_cli_streaming.py`、`tests/test_cli_mcp_trust_prompt.py` 等 Textual 桩为 `InlineRepl` 或 terminal 模块桩。
2. 新增 `tests/test_cli_terminal.py`：
   - 静态区：反色用户行、`onecode>` 前缀（snapshot 或 Rich export 文本断言）。
   - 补全：`suggestions_for` + Completer 元数据；Enter vs Tab 行为（单元级 key binding 或 mock Application）。
   - transient：fake stdout 捕获 DEC 1049 序列；page 退出后无 page 正文在「主 buffer」。
   - queue：mock stream 延迟，验证第二条在第一条完成后执行。
3. 更新 `docs/design-docs/cli-architecture.md`、`docs/design-docs/cli-message-rendering-architecture.md`（若存在 Textual 描述则改为 terminal/）。
4. 全量：`uv run python -m pytest tests -q`；`uv run python -m compileall ui/cli`；`rg -i textual` 在仓库内仅允许出现在历史 ExecPlan 文档中。

验收：367+ 测试通过（数量可能随新增略增）；无 Textual import；架构文档描述内联模型。

## Concrete Steps

工作目录：仓库根 `D:\study\OneCode`（Windows PowerShell）。

1. 同步环境：`uv sync --dev`；`.\.venv\Scripts\Activate.ps1`。

2. 里程碑 0 完成后运行 spike：

    uv run python -m ui.cli.terminal._spike

3. 每里程碑后：

    uv run python -m compileall ui/cli
    uv run python -m pytest tests -q

4. 手动 TTY 验收：

    uv run python -m ui.cli.app

5. batch 回归：

    "你好" | uv run python -m ui.cli.app

## Validation and Acceptance

**里程碑 0**：spike 在浅/深终端下边框与反色正确；动态区擦除；live Markdown 不崩溃。

**里程碑 1**：`python -m ui.cli.app` 启动；无 Textual；横幅打印；输入回车后反色用户行出现在 scrollback。

**里程碑 2**：mock 助手输出含 `onecode>`；工具摘要进静态区。

**里程碑 3**：`/` 补全列表与 Tab/Enter 语义正确；运行中队列可见且可追加。

**里程碑 4**：真实对话 live Markdown；Esc 取消；队列串行执行。

**里程碑 5**：`/status`、`/resume` Esc 后 scrollback 无临时页；权限/trust/connect 可用。

**里程碑 6**：pytest 全绿；`rg textual ui/cli tests` 无代码引用；文档更新。

**整体终验**：浅色终端下完成「提问 → live 流式 → 工具横幅 → `/status` → Esc → `/resume` 选择或取消 → Esc → 滚动 scrollback 仅有对话与定稿输出、无临时页」；切换到深色终端重复一次背景继承检查。

## Idempotence and Recovery

- `uv add` / `uv remove` 可重复执行；锁文件冲突时 `uv lock` 再 `uv sync`。
- 里程碑 1 删除 `tui/` 前确保里程碑 0 spike 通过；建议独立 git 分支，每里程碑一提交。
- 若 live Markdown 不稳定，Decision Log 可记录回退为「流式纯文本 + 完成时 Markdown 定稿」，但默认按 live_md 实现。
- batch 路径全程保持可用，作为 TTY 重构失败时的后备。
- `.env`、`.onecode/` 会话数据不被本计划结构性修改。

## Artifacts and Notes

### 静态区用户行与助手行（示意）

    # static_output.py 目标行为（示意，非最终代码）
    def print_user_submitted(console, line, *, theme):
        # 反色：浅色终端用 black on white，深色用 white on black
        console.print(Text(f"> {line}", style=theme.user_input_reverse))

    def print_assistant_markdown(console, md_text, *, theme):
        console.print(Text("onecode>", style=theme.assistant_prefix), end="")
        console.print(Markdown(md_text))

### InlineRepl 主循环（示意）

    class InlineRepl:
        def run(self) -> None:
            print_banner()
            while True:
                line = prompt_session.read(runtime)  # erase_when_done
                if not line: continue
                print_user_submitted(line)
                if line.startswith("/"):
                    result = dispatch_command(runtime, line)
                    self.handle_command(result)
                elif self.agent_running:
                    self.queue.push(line)
                else:
                    asyncio.run(self.run_agent(line))

### 与旧 Textual 计划的关系

`textual-cli-ui-refactor-execplan.md` 已完成其「用 Textual 替换手写 prompt_input」的历史目标，但产品方向已改为内联终端体验。本计划 **不修改** 该文件；实现完成后可将 Textual 计划移至 `docs/exec-plans/completed/` 并注明被本计划取代，或保留作历史记录。

### 不可改动的运行时接口

- `core/stream_events.py` 事件协议
- `ui/cli/commands.py::dispatch_command`
- `ui/cli/types.py::CliRuntime`、`CommandResult`
- `services/permissions` 的 `PermissionPrompter` 协议
- `tests/test_import_boundaries.py` 约束的依赖方向

---

修订说明：2026-06-13 初版。依据用户确认的路线 A（内联 prompt_toolkit + Rich）、自动终端主题、排队输入、简单 status 页、live Markdown、彻底移除 Textual，以及 Claude Code 参考 UI 四项交互需求撰写。

修订说明：2026-06-13 收口。里程碑 0-6 已完成，测试清零与文档更新完成，本计划从 `docs/exec-plans/active/` 移至 `docs/exec-plans/completed/`。旧 Textual 方向计划只保留为历史记录，不再代表当前实现目标。
