# 用 Textual 框架彻底重写 OneCode 的 CLI 交互与渲染层

> Historical note, 2026-06-13: this plan records the replaced Textual direction. The current CLI implementation is the inline `prompt_toolkit` + Rich terminal model described by `docs/exec-plans/completed/cli-inline-terminal-ui-refactor-execplan.md`; Textual code and dependencies have been removed.

本 ExecPlan 是一份"活文档"。`Progress`、`Surprises & Discoveries`、`Decision Log`、`Outcomes & Retrospective` 四个章节必须随工作推进持续更新。

本仓库的 ExecPlan 规范见仓库根目录 `PLANS.md`，本文档必须按 `PLANS.md` 的要求维护（自包含、面向新手、以可观察行为验收、记录决策与发现）。

## Purpose / Big Picture

今天的 OneCode 自带一个"手写的终端 UI"：它用一套自研的终端按键引擎（位于 `ui/cli/prompt_input/`）逐字符读取键盘、自己管理光标、自己进入和退出"备用屏幕"（备用屏幕指终端的第二块画布，进入后原有滚动内容被隐藏，退出后恢复，靠 DEC 1049 转义序列控制），再用 Rich 库（一个把带样式文本打印到终端的 Python 库）逐行 `print` 输出。这套实现可用，但维护成本高、跨平台（尤其 Windows）分支多、且能力受限：助手回复是逐字 `print` 的纯文本、工具调用的"开始/进行中"过程不显示、没有真正的多区域布局。

本次工作用 **Textual**（一个用于构建终端图形界面的 Python 框架，官方包名 `textual`，最新稳定版 8.2.7，基于 Rich 之上提供窗口、控件、CSS 式样式、事件循环和鼠标支持）**彻底替换** `ui/cli/` 的交互与渲染外壳。完成后用户能获得：

- 一个常驻的全屏终端界面：顶部一行标题（显示 `OneCode`、当前模型名、当前工作目录），中间是可滚动的对话历史区，下方是实时流式预览区与一行状态栏，最底部是多行输入框（回车发送，Shift+回车换行）。
- 助手回复以 Markdown 渲染（标题、代码块、列表等有样式），而不是纯文本逐字打印。
- 工具调用显示完整生命周期：准备 → 执行中（带计时）→ 完成（带结果摘要）。
- 鼠标拖拽选中对话历史中的文本并用 Ctrl+C 复制到系统剪贴板。
- 斜杠命令（如 `/status`）和 `@文件` 输入时的内联自动补全。
- 权限确认、MCP 服务器信任确认、`/connect` 配置向导、`/resume` 会话选择都以界面内的面板或模态窗呈现（模态窗指覆盖在主界面之上、需要用户先处理完才返回主界面的临时窗口）。

如何看到它工作：在仓库根目录运行 `uv run python -m ui.cli.app`，会出现上述全屏界面；输入一句话回车，助手回复会流式出现在预览区、结束后定格到历史区；输入 `/status` 回车会弹出运行时状态模态窗，按 `Esc` 返回。

这次是"替换式"重构，不是并存：旧的 `ui/cli/prompt_input/` 整个目录、旧的逐字 `print` 流式逻辑、以及围绕它们的旧测试都将被删除并以 Textual 版本重写。**入口命令保持不变**（仍是 `python -m ui.cli.app`）。

关键前提（必须始终成立，否则本计划无效）：OneCode 的"界面层"与"运行时层"早已通过一个类型化的事件流解耦——运行时核心 `core/`、各类服务 `services/`、基础设施 `infrastructure/` **完全不需要改动**。本计划只改写 `ui/cli/` 这一层。

## Progress

- [x] (2026-06-13 14:30Z) 完成现状调研：读通 `ui/cli/` 全部交互与渲染入口、`core/stream_events.py` 事件协议、`CliPermissionPrompter` 为 async、`build_runtime`/`CliRuntime`/`dispatch_command`/`CommandResult` 接口；确认 Textual 最新稳定版为 8.2.7。
- [x] (2026-06-13 14:30Z) 与用户敲定三项关键决策（入口不变、build_runtime 移入界面挂载后的后台任务并配启动加载界面、状态栏仅显示 OneCode/模型/cwd）。见 `Decision Log`。
- [x] (2026-06-13) 里程碑 0：加入 `textual>=8.2.7`；`ui/cli/tui/_spike.py` 验证 RichLog 选区 + Ctrl+C；内置选区不够稳定，采用 `MessageLog` 手写选区（同参考项目模式）。
- [x] (2026-06-13) 里程碑 1–5：`OneCodeApp`/`MainScreen` 五区域布局、LoadingScreen 装配 worker、`run_agent` 事件流、Textual 权限/MCP trust、命令/补全/模态窗、Esc 退出与复制。
- [x] (2026-06-13) 里程碑 6：删除 `prompt_input/` 与 `pages.py`；batch 路径保留；新增 `tests/test_cli_tui.py`；更新 `cli-architecture.md`；367+ 测试通过。
- [x] (2026-06-13) 非交互 batch 路径：`batch.py` + `main()` TTY 分流。

## Surprises & Discoveries

- 观察：现有架构对本次替换异常友好——界面与运行时只通过 `core/stream_events.py` 的 `AgentEvent`（一个带 `type` 字段的冻结 dataclass）通信，且 `tests/test_import_boundaries.py` 的依赖约束只管 `core/services/infrastructure`，不约束 `ui/`。因此替换 UI 不触碰任何受约束代码。
  证据：`ui/cli/app.py` 的 `main_loop_async()` 通过 `async for event in runtime.loop.stream(...)` 消费事件；`architecture.md` 明确"CLI 是 UI 的一种实现，不直接承载 runtime 逻辑"。
- 观察：权限确认 `CliPermissionPrompter.request_permission()` 已经是 `async def`，并在工具执行器的 async 调用链中被 `await`。由于 agent 主循环将运行在 Textual 的同一个 asyncio 事件循环里，权限请求可以用一个 `asyncio.Future` 直接桥接到界面，**无需跨线程**。
  证据：`ui/cli/permissions.py` 第 33 行 `async def request_permission`；参考实现 `docs/references/ui/coomi/ui/textual_app.py` 第 854 行用 `self._question_future = asyncio.get_event_loop().create_future(); result = await self._question_future` 处理同类"需回传用户选择"的交互。
- 观察：Textual 8.x 内置跨控件文本选择与 `App.copy_to_clipboard()`（通过 OSC 52 写系统剪贴板），可能无需照搬参考项目里手写的 `SelectableRichLog`。里程碑 0 需先验证内置能力是否够用，再决定是否自定义。
  证据：参考项目 `docs/references/ui/coomi/ui/widgets/selectable_rich_log.py` 用 `ALLOW_SELECT = True` 与 `get_selection()`/`Selection.extract()`，这些正是 Textual 较新版本的内置选择 API。
- 观察（2026-06-13 实现）：`CSS_PATH = "theme.tcss"` 在测试子类中会相对测试模块路径解析，导致 `StylesheetError`；改为 `Path(__file__).with_name("theme.tcss")` 绝对路径后解决。
- 观察（2026-06-13 实现）：`MessageLog` 采用参考项目的鼠标拖选实现（非 Textual 内置选区 API），与 `_spike.py` 验证一致。

## Decision Log

- Decision: 入口命令保持 `python -m ui.cli.app` 不变，重写发生在 `ui/cli/` 包内部，不新建 `ui/tui/` 并存。
  Rationale: 用户明确要求"入口不变、彻底重构、不为保留代码而保留代码"。沿用包名可让现有文档、脚本、`pyproject` 入口无需改动。
  Date/Author: 2026-06-13 / 用户与执行 agent。
- Decision: `build_runtime()` 不在启动 Textual 之前阻塞执行，而是放进界面挂载后的后台任务里执行，期间显示一个"启动加载界面"；启动期的 MCP 服务器信任确认改用 Textual 模态窗。
  Rationale: 用户选择"更统一但改动更大"的方案，让所有交互（含启动期信任确认）都在 Textual 内完成，避免启动期出现非 Textual 的裸 `print` 提示。
  Date/Author: 2026-06-13 / 用户与执行 agent。
- Decision: 状态栏只显示 `OneCode`、当前模型名、当前工作目录三项，不显示 compaction/后台任务/记忆/turn 计数等。
  Rationale: 用户明确要求"就 model、cwd、OneCode 名称就好了"，保持状态栏简洁。
  Date/Author: 2026-06-13 / 用户与执行 agent。

## Outcomes & Retrospective

（实现推进到各里程碑或全部完成时填写：实际达成了什么、与 Purpose 的差距、遗留项、经验教训。）

## Context and Orientation

本节假设读者对本仓库一无所知。请先建立心智模型，再动手。

### 这个项目是什么

OneCode 是一个"代码 agent 运行时"：它接收用户的一句话，组装上下文与系统提示，调用大模型（LLM），按模型要求执行工具（读写文件、运行命令等），把结果再喂回模型，循环直到模型给出最终回答。运行时核心在 `core/`，各能力服务在 `services/`，可替换的边界（模型 provider、配置、文件系统）在 `infrastructure/`，命令行界面在 `ui/cli/`。架构总览见仓库根 `architecture.md`，CLI 现状见 `docs/design-docs/cli-architecture.md` 与 `docs/design-docs/cli-message-rendering-architecture.md`。

### 关键术语（本文用到的都在此定义）

- "事件流 / `AgentEvent`"：运行时主循环 `AgentLoop`（`core/loop.py`）在处理一句用户输入时，会逐步产出一串"事件"。每个事件是 `core/stream_events.py` 里定义的 `AgentEvent`（一个冻结 dataclass），带一个 `type` 字符串字段和若干可选字段。界面通过 `async for event in loop.stream(...)` 逐个消费这些事件来更新画面。可能的 `type` 取值（见 `core/stream_events.py` 的 `AgentEventType`）：`interaction_started`、`assistant_delta`、`assistant_message_completed`、`tool_call_ready`、`tool_started`、`tool_progress`、`tool_result`、`transition`、`completed`、`error`。其中 `assistant_delta` 的 `text` 字段是模型流式输出的一小段文本；`tool_result` 的 `result` 字段是 `services/tools/types.py` 的 `ToolExecutionResult`；`completed` 的 `text` 是最终完整回答；`error` 表示本轮出错。
- "运行时容器 / `CliRuntime`"：`ui/cli/types.py` 里的 dataclass，聚合了一次会话用到的所有运行时组件（工作目录 `workspace`、运行状态 `state`、消息存储 `message_store`、主循环 `loop`、工具注册表 `registry`、模型标签 `provider_label`、模型名 `model`、权限 prompter `permission_prompter`、附件收集器 `attachment_collector`、MCP 管理器 `mcp_manager`、各类记忆与任务服务等）。命令和视图层都从它读数据。它还提供 `with_session(...)`（开新会话/恢复会话时重建会话级组件）和 `with_model_config()`（`/connect` 后重读 `.env` 重绑模型相关对象）两个方法，返回新的 `CliRuntime`。
- "装配函数 / `build_runtime(workspace) -> CliRuntime`"：`ui/cli/app.py` 里唯一的应用装配入口，按依赖顺序创建上面所有组件。它内部会执行两个会阻塞或需要用户交互的步骤：`_prompt_for_project_mcp_trust(...)`（对未信任的本地 MCP 服务器逐个询问是否信任）和 `mcp_manager.connect_all_blocking()`（同步连接所有已配置的 MCP 服务器）。"MCP"指 Model Context Protocol，一种让外部进程向 agent 暴露额外工具的协议。
- "命令分发 / `dispatch_command(runtime, line) -> CommandResult`"：`ui/cli/commands.py` 里把以 `/` 开头的整行解析并执行对应处理器，返回 `CommandResult`（`ui/cli/types.py`）。`CommandResult` 字段：`should_exit`（是否请求退出）、`runtime`（若非空表示要替换当前 `CliRuntime`，用于 `/clear`、`/resume`、`/connect`）、`renderable`（要显示的内容，通常是 Rich 对象或字符串）、`presentation`（`"inline"` 表示直接打印进历史、`"page"` 表示需要整屏分页显示）、`interaction`（`"resume_selector"` 或 `"connect"`，表示需要界面接管的多步交互）。当前可见命令：`/status`、`/usage`、`/memory`、`/permissions`、`/skills`、`/tasks`、`/mcp`、`/compact [focus]`、`/resume [target]`、`/connect`、`/clear`、`/exit`。
- "视图 / `views/`"：`ui/cli/views/` 下的函数（如 `render_status`、`render_usage`、`render_mcp`、`render_memory`、`render_permissions`、`render_skills`、`render_tasks`、`render_session_summaries` 等），它们只读 `CliRuntime` 和入参，返回 Rich 可渲染对象（`rich.console.Group`、`rich.table.Table`、`rich.text.Text` 等）。这些函数被 `ui/cli/renderer.py` 转发。它们与终端类型无关，可以原样塞进 Textual 控件显示，是本次重构的**复用资产**。
- "工具结果摘要 / `tool_renderers.py`"：`ui/cli/tool_renderers.py` 按工具名把 `ToolExecutionResult.metadata` 渲染成一行简短文本（如 `[read_file] Read 82 line(s) from ...`）。也是复用资产。
- "权限 prompter / `CliPermissionPrompter`"：`ui/cli/permissions.py` 里在工具执行前弹出权限确认面板并读取用户选择，返回 `PermissionResponse`（`services/permissions` 提供）。本次会用一个 Textual 版替换它。
- "Textual App / Screen / Widget"：Textual 的三层概念。`App` 是应用主体（持有事件循环、全局状态、剪贴板）；`Screen` 是一整屏（可压栈，弹模态窗就是 `push_screen`）；`Widget` 是屏内控件（如输入框、日志区、状态栏）。控件用 `compose()` 声明子控件，用 CSS 式的 TCSS 文件或类属性 `CSS`/`DEFAULT_CSS` 控制布局与配色。控件可重写 `render()` 返回一个 Rich 对象（"立即模式"），或继承内置控件并调用其方法更新内容（"托管模式"，如 `RichLog.write(...)`、`Static.update(...)`、`TextArea`）。后台耗时任务用 `@textual.work` 装饰器跑成"worker"，worker 与界面在同一事件循环上协作。

### 现状关键文件（全部在 `ui/cli/`，全路径）

- `ui/cli/app.py`：入口 `main()`、装配 `build_runtime()`、主循环 `main_loop_async()`、`/resume` 选择器协程、MCP 信任确认 `_prompt_for_project_mcp_trust()`、长期记忆后台钩子 `_start_long_term_memory_dream()`。
- `ui/cli/prompt_input/`（整个目录，将被删除）：自研终端输入引擎（`state.py` 状态、`events.py` 事件归一、`editor.py` 编辑、`reducer.py` 语义、`terminal.py` 终端设备与备用屏幕、`session.py` 对外 `read_prompt`/`read_text`/`read_confirm`/`select_item`/`show_page`/`transient_terminal_scope`、`suggestions.py` 补全数据）。
- `ui/cli/input.py`：非交互 batch 助手 `read_batch_line(label)`，stdin 非 TTY 时读一行。**保留**（batch 路径要用）。
- `ui/cli/commands.py`：命令注册表与 `dispatch_command`。**复用**（界面层调用它）。
- `ui/cli/pages.py`：对 `prompt_input.session` 的薄封装（`show_page`、`select_item`）。**删除**（界面层直接用 Textual 模态窗与选择器替代）。
- `ui/cli/resume.py`：会话扫描、标题派生、`restore_runtime_from_target`、`list_session_summaries`、`SessionSummary`。**复用**（数据逻辑）。
- `ui/cli/connect.py`：`/connect` 流程与 `.env` 写入 `write_provider_env`。**拆分复用**：`write_provider_env` 与 `ProviderConnectionService().list_connect_options()` 保留，多步交互改写为 Textual 向导。
- `ui/cli/permissions.py`：交互式权限确认面板与 `CliPermissionPrompter`。**重写**为 Textual 版（面板渲染函数可复用）。
- `ui/cli/renderer.py`：Rich 输出入口与一批 `render_*` 函数（`render_banner`、`render_status`、`render_usage`、`render_memory`、`render_permissions`、`render_skills`、`render_tasks`、`render_mcp_status`、`render_compact`、`render_clear`、`render_resume`、`render_restored_messages`、`render_error`、`render_group`、`render_tool_result_summary`、`render_to_text(renderable, width=...)`）。**大部分复用**：除 `print_renderable`（直接打到 stdout，界面层不再用）和逐字流式 helper 外，`render_*` 系列都返回 Rich 对象，可直接塞进 Textual。
- `ui/cli/tool_renderers.py`：工具结果摘要。**复用**。
- `ui/cli/views/`：各状态视图。**复用**。
- `ui/cli/theme.py`：Rich 样式名与 Unicode 状态符号 `SYMBOLS`。**复用**（Rich 主题仍用于 renderable；另加一个 Textual TCSS 控制布局/底色）。
- `ui/cli/types.py`：`CliRuntime`、`CommandResult`。**复用，不改**。

### 运行时事件流的事实（实现里程碑 2 必须依据）

`ui/cli/app.py` 现在这样消费一句普通输入：

    attachments = await runtime.attachment_collector.collect_for_user_turn(
        line, runtime.state, runtime.message_store.current_messages(), is_main_thread=True,
    )
    async for event in runtime.loop.stream(line, attachments=attachments):
        if event.type == "assistant_delta": ...     # event.text 是一小段文本
        elif event.type == "tool_result": ...        # event.result 是 ToolExecutionResult
        elif event.type == "completed": ...           # event.text 是最终完整回答

工具调用的中间事件（`tool_call_ready`、`tool_started`、`tool_progress`）当前被丢弃——本次要把它们渲染成工具横幅。这些事件的字段已核实（见 `core/loop.py`）：`tool_call_ready` 的 `metadata["tool_call"]` 是完整工具调用对象（工具名需从中提取）；`tool_started` 与 `tool_progress` 的 `metadata` 同时带 `tool_name` 与 `tool_call_id`（`tool_progress` 还带 `text` 进度文本）；`tool_result` 用 `result` 字段（`ToolExecutionResult`，含其自身的 `tool_name`/`tool_call_id`）。横幅按 `tool_call_id` 作为键最稳妥。权限确认**不走事件流**：它发生在工具执行器内部，通过 `await runtime.permission_prompter.request_permission(request)` 同步等待用户。

### 参考实现（只读，不要直接拷贝业务代码）

`docs/references/ui/coomi/ui/` 是一个用 Textual 写的同类 agent 界面，可作为模式参考：`textual_app.py`（App 主控、`@work` 消费事件、`asyncio.Future` 处理需回传的交互）、`screens/main_screen.py`（五区域 `compose`）、`widgets/streaming_preview.py`（流式预览，50ms 节流）、`widgets/status_panel.py`（状态栏立即模式 + 状态机）、`widgets/prompt_text_area.py`（回车发送/Ctrl+Enter 换行）、`widgets/tool_call_banner.py`（工具生命周期）、`widgets/selectable_rich_log.py`（手写选中，仅在内置选择不够用时参考）、`tcss/coomi.tcss`（布局与配色基线）。注意：参考项目的事件类型、命令处理、provider 管理与 OneCode 不同，只借鉴"怎么用 Textual"，业务一律对接 OneCode 自己的 `CliRuntime`/`AgentEvent`/`dispatch_command`。

## Plan of Work

总体顺序：先用一个独立 spike 验证 Textual 的关键能力（里程碑 0），再搭界面骨架并把装配搬进界面（里程碑 1），接通主对话流（里程碑 2），桥接权限与信任（里程碑 3），接通命令与多步交互（里程碑 4），打磨选中复制/主题/状态栏/退出（里程碑 5），最后删旧代码、重写测试、更新文档（里程碑 6）。每个里程碑都能独立运行与验收。

下面给出目标目录结构（重构后的 `ui/cli/`）。新增文件在 `ui/cli/tui/` 子包下，复用文件留在原处，删除文件在里程碑 6 移除。

    ui/cli/
      app.py            # 改写：main() 判断 TTY；TTY → 启动 OneCodeApp；非 TTY → batch 路径
      batch.py          # 新增：非交互 batch（由原 input.py + 原 main_loop 的流式打印演化）
      input.py          # 保留：read_batch_line
      commands.py       # 复用不改
      resume.py         # 复用不改
      connect.py        # 拆分：保留 write_provider_env / 选项列举；多步交互迁到 tui
      permissions.py    # 保留面板渲染函数；CliPermissionPrompter 由 tui 版替换
      renderer.py       # 复用 render_* 系列
      tool_renderers.py # 复用不改
      theme.py          # 复用；另加 Textual 用法
      types.py          # 复用不改
      views/            # 复用不改
      tui/
        __init__.py
        app.py          # OneCodeApp(App)：装配 worker、事件 worker、Future 桥接、命令路由
        loading_screen.py   # 启动加载 Screen（build_runtime 进行中）
        main_screen.py      # MainScreen：Header + MessageLog + StreamingPreview + StatusPanel + PromptInput
        page_screen.py      # PageScreen(ModalScreen)：显示 Rich renderable，Esc 关闭
        select_screen.py    # SelectScreen(ModalScreen)：列表选择（/resume）
        connect_screen.py   # ConnectScreen(ModalScreen)：/connect 多步向导
        permission.py       # TextualPermissionPrompter + PermissionScreen(ModalScreen)
        trust.py            # MCP 信任确认 Screen + 注入 build_runtime 的回调
        widgets/
          __init__.py
          message_log.py    # 历史区（基于 RichLog，启用文本选择）
          streaming_preview.py
          status_panel.py
          prompt_input.py   # 多行输入框：回车发送/Shift+Enter 换行/补全触发
          tool_banner.py    # 工具生命周期（数据类，build() 返回 Rich Table 写入历史区）
          command_list.py   # 斜杠/文件内联补全列表
        theme.tcss          # Textual 样式表（布局 + 暗色配色）
      prompt_input/     # 里程碑 6 删除
      pages.py          # 里程碑 6 删除

### 装配的可注入信任回调（里程碑 1/3 需要）

`build_runtime` 当前内部直接调用 `_prompt_for_project_mcp_trust(...)`（裸 `print` + `read_confirm_sync`）。为让信任确认在 Textual 内完成，把信任确认抽成一个可注入回调：

在 `ui/cli/app.py` 把 `build_runtime` 的签名扩展为：

    def build_runtime(
        workspace: Path,
        *,
        trust_prompt: Callable[[McpTrustPromptRequest], "trust"|"skip"] | None = None,
    ) -> CliRuntime: ...

其中 `McpTrustPromptRequest` 是一个小 dataclass，承载要展示给用户的字段（server 名、command、args、cwd、显式 env keys、base env keys）。当 `trust_prompt is None` 时，沿用旧的裸 `print`/`read_confirm_sync` 行为（供 batch 路径与测试用）；当传入回调时，用回调获取 `"trust"`/`"skip"`。Textual 侧在装配 worker 里传入一个回调，回调内 `push_screen` 一个 `TrustScreen` 并用 `Future` 等待结果（见里程碑 3）。这样 `build_runtime` 不再写死交互方式，且不破坏现有非交互行为。

### 主界面布局（里程碑 1）

`MainScreen.compose()` 自上而下产出五个控件：

- `Header`（Textual 内置或自定义）：docked 在顶部，高度 1，显示 `OneCode  ·  <模型名>  ·  <cwd>`。
- `MessageLog`（继承 `RichLog`）：高度 `1fr`（占满剩余空间），存放对话历史、工具横幅、命令的 inline 结果。启用 `markup=True, wrap=True`，并开启文本选择。
- `StreamingPreview`（继承 `Static`）：高度 `auto`、最大约 10 行，显示"思考中/工具执行中"以及流式 Markdown 预览（50ms 节流）。
- `StatusPanel`（继承 `Widget`，重写 `render()`）：高度 1~2，显示 OneCode/模型/cwd 与运行态（空闲/执行中/取消提示）。
- `PromptInput`（继承 `TextArea`）：docked 在底部，高度约 5；回车提交、Shift+Enter 换行；内容以 `/` 开头或包含 `@` 时触发补全。

### 主对话流 worker（里程碑 2）

在 `OneCodeApp` 里用 `@work(exclusive=True)` 定义 `run_agent(line: str)`：先收集附件（`runtime.attachment_collector.collect_for_user_turn(...)`），再 `async for event in runtime.loop.stream(line, attachments=...)`，按 `event.type` 分发：

- `assistant_delta`：累加到内部缓冲 `self._stream_buffer`，调用 `preview.show_text(buffer)`（节流刷新）。
- `tool_call_ready` / `tool_started`：为该工具创建/更新 `ToolBanner`，`preview.show_tool(tool_name)`。
- `tool_progress`：更新对应 banner 为"执行中（计时）"。
- `tool_result`：用 `renderer.render_tool_result_summary(event.result, workspace=runtime.workspace)` 得到摘要，并让 banner 定格，`log.write(...)` 写入历史。
- `error`：`log.write` 红色错误块，并 `runtime.error_log_recorder.record_error(exc, source="cli_main_loop", ...)` + flush。
- `completed`：`event.text` 作为最终回答；流结束后把 `self._stream_buffer` 用 `rich.markdown.Markdown` 写入历史区，清空预览区。

worker 期间 `StatusPanel` 设为"执行中"，并用 `set_interval(0.08, tick)` 驱动 spinner。worker 结束（finally）恢复状态栏空闲、清空预览。

### 权限与信任桥接（里程碑 3）

在 `ui/cli/tui/permission.py` 定义 `TextualPermissionPrompter`，签名与现有一致：

    class TextualPermissionPrompter:  # 实现 services.permissions.PermissionPrompter 协议
        def __init__(self, app: "OneCodeApp") -> None: ...
        async def request_permission(self, request: PermissionRequest) -> PermissionResponse: ...

`request_permission` 内：创建 `future = asyncio.get_event_loop().create_future()`，`self._app.call_from_thread` 不需要（同循环），直接 `self._app.push_screen(PermissionScreen(request), callback=lambda resp: future.set_result(resp))`，然后 `return await future`。`PermissionScreen` 是 `ModalScreen`，用 `ui/cli/permissions.py` 现有的 `render_permission_panel(request)` 渲染面板，按键映射到允许一次/允许本会话/拒绝并 `dismiss(response)`。把这个 prompter 注入到 `build_runtime` 创建的链路里：最简单做法是装配时仍用一个占位 prompter，装配完成后用 `runtime.permission_prompter` 字段替换，并把它一并设置进 `tool_executor` 与 `subagent_runner` 已持有的引用——为避免深改，里程碑 3 评估两种落地：(a) 给 `build_runtime` 增加可选参数 `permission_prompter`；(b) 用一个轻量适配器对象在装配时即注入、运行期再绑定 `app`。优先 (a)，因为更直接、可测试。

MCP 信任：装配 worker 传入的 `trust_prompt` 回调内 `push_screen(TrustScreen(req), callback=...)` 并 `await` future，返回 `"trust"`/`"skip"`。

### 命令与多步交互（里程碑 4）

输入提交时：若文本以 `/` 开头，调用 `dispatch_command(runtime, line)` 得到 `CommandResult`：

- `result.interaction == "resume_selector"`：扫描 `list_session_summaries(runtime.workspace)`，`push_screen(SelectScreen(...))` 选择，选中后 `restore_runtime_from_target(...)` 得到新 runtime，替换 `self.runtime`，再 `push_screen(PageScreen(renderer.render_restored_messages(...)))` 显示历史。
- `result.interaction == "connect"`：`push_screen(ConnectScreen(...))` 跑多步向导（选 provider → 可选 base URL → API key 隐藏输入 → model），完成后 `write_provider_env(...)` + `runtime.with_model_config()` 替换 runtime。
- 否则按 `result.presentation`：`"page"` → `push_screen(PageScreen(result.renderable))`（模态窗内用一个 `RichLog`/`Static` 显示，`render_to_text` 不再需要，因为 Textual 控件能直接吃 Rich 对象，但需确认宽度自适应）；`"inline"` → `log.write(result.renderable)`。
- `result.runtime` 非空 → 替换 `self.runtime`。
- `result.should_exit` → `self.exit()`。

补全：输入框内容变化时，调用 `suggestions_for(runtime, text, cursor)`（来自 `prompt_input/suggestions.py` 的逻辑——里程碑 6 前可临时从该模块导入，里程碑 6 删除 `prompt_input` 时把 `suggestions_for` 迁移到 `ui/cli/tui/` 或独立模块）。把建议项渲染到 `CommandList` 控件，上下键选择、回车采纳。

### 非交互 batch 路径（贯穿）

`ui/cli/app.py` 的 `main()`：

    def main(argv=None) -> int:
        workspace = Path.cwd()
        if not sys.stdin.isatty():
            return run_batch(workspace)        # 不启动 Textual
        try:
            ... # Textual 路径：见下
        ...

`run_batch` 用 `build_runtime(workspace)`（不传 `trust_prompt`，沿用裸 print），读 stdin 一行（`read_batch_line`），`async for event in loop.stream(...)` 并用 `print` 输出（复用 `renderer` 的纯文本/Markdown 转换）。这保证管道/CI 场景仍可用，且不依赖 TTY。

TTY 路径：`OneCodeApp(workspace).run()`。`OneCodeApp.on_mount` 里 `push_screen(LoadingScreen())` 并启动装配 worker；worker 完成后 `pop` 加载界面、`push_screen(MainScreen(...))`、显示欢迎。

## Concrete Steps

所有命令在仓库根目录 `D:\study\OneCode`（Windows PowerShell）运行；先激活虚拟环境。

1. 同步依赖并激活环境：

    uv sync --dev
    .\.venv\Scripts\Activate.ps1

2. 加入 Textual 依赖（编辑 `pyproject.toml` 的 `dependencies`，加入 `"textual>=8.2.7"`，开发期可另加 `"textual-dev>=1.7.0"` 到 dev 组用于调试控制台），然后：

    uv add "textual>=8.2.7"
    uv add --dev "textual-dev"

   预期：`pyproject.toml` 出现 textual 依赖，`uv.lock` 更新，`uv run python -c "import textual, textual.app; print(textual.__version__)"` 打印类似 `8.2.7`。

3. 里程碑 0 spike：在 `ui/cli/tui/_spike.py` 写一个最小 App（一个 `RichLog` + 一个 `TextArea`，输入回车把文本写进 log；鼠标拖选 log 文本，Ctrl+C 触发 `self.copy_to_clipboard(selected)`）。运行：

    uv run python -m ui.cli.tui._spike

   预期：出现全屏界面；输入回车文本进入上方日志；鼠标可拖选日志文本并高亮；Ctrl+C 后在外部编辑器粘贴能得到所选文本（若所在终端支持 OSC 52）。记录内置选择是否够用到 `Surprises & Discoveries`。spike 文件在里程碑 1 转正或删除。

4. 后续每个里程碑实现后，运行该里程碑的验收（见下一节），并运行：

    uv run python -m compileall ui/cli
    uv run python -m pytest tests -q

   在里程碑 6 之前，旧 `prompt_input` 测试可能仍在；允许这些测试暂时通过/跳过，但**不得新增对旧 `prompt_input` 的依赖**。里程碑 6 删除旧测试并重写。

5. 启动完整界面（里程碑 1 之后任意时刻）：

    uv run python -m ui.cli.app

   非交互验证（不启动 Textual 的 batch 路径，确认未被破坏）：

    "你好" | uv run python -m ui.cli.app

   预期：batch 路径读到一行、运行一轮、把回答打印到 stdout 后退出（行为与现状一致）。

## Validation and Acceptance

以行为验收，每个里程碑都要能"看到它工作"。下面给出每个里程碑的可观察验收。

里程碑 0（spike）：运行 `uv run python -m ui.cli.tui._spike`，出现全屏界面；输入"abc"回车后上方日志出现"abc"；鼠标拖选"abc"出现高亮；Ctrl+C 后能在别处粘贴出"abc"。结论（内置选择是否够用）记入文档。

里程碑 1（骨架 + 装配）：运行 `uv run python -m ui.cli.app`，先短暂出现"正在启动 OneCode…"加载界面（装配/MCP 连接进行中），随后进入主界面：顶部一行 `OneCode · <模型名> · <cwd>`，底部多行输入框，中部空历史区与状态栏。输入框可打字、回车把输入回显进历史区（此里程碑暂不接 agent）。按 Ctrl+C 不崩溃。`uv run python -m pytest tests -q` 不新增失败。

里程碑 2（主对话流）：在主界面输入"用一句话介绍你自己"回车，预览区出现流式文本（Markdown），结束后定格到历史区；若模型触发工具，历史区出现工具横幅（准备/执行中/完成摘要）。制造一次错误（例如把 `.env` 的模型名改错再启动并提问）应在历史区看到红色错误块，且 `.onecode/<session>/errors.jsonl` 新增一条 `source=cli_main_loop` 记录。

里程碑 3（权限/信任）：在一个尚未授予权限的工作区，让 agent 去写文件，应弹出权限模态窗（显示工具与目标路径），选择"拒绝"后该工具返回被拒结果、agent 继续；选择"允许本会话"后同类操作不再追问。若项目配置了未信任的本地 MCP 服务器，启动时应弹出信任模态窗而不是裸文字提示；选择 skip 后该服务器不连接。

里程碑 4（命令/交互）：输入 `/status` 回车弹出状态模态窗，`Esc` 返回主界面且主界面内容保持不变。输入 `/` 出现命令补全列表，上下键移动、回车采纳。输入 `@` 后跟路径前缀出现文件补全。`/resume` 无参数弹出会话选择器，选中后历史以分页模态窗展示且当前会话被替换。`/connect` 走完向导后 `.env` 被更新、状态栏模型名变化。`/clear` 开新会话并清空历史区。`/exit` 退出程序且 transcript/trace/errors 已 flush、MCP 已关闭。

里程碑 5（选中/主题/状态栏/退出）：鼠标拖选历史区文本，Ctrl+C 复制成功。状态栏稳定显示 OneCode/模型/cwd。agent 运行中按一次 Esc 取消本轮（历史区出现"已取消"），空闲时按一次 Esc 出现"再按一次 Esc 退出"提示，2 秒内再按 Esc 退出。

里程碑 6（清理/测试）：`ui/cli/prompt_input/` 与 `ui/cli/pages.py` 已删除；`rg "prompt_input"` 在 `ui/` 下无残留引用（batch 路径不依赖它）。新测试用 `App.run_test()` 覆盖：提交一句输入并断言历史区出现回答（用一个假的 `loop.stream` 产出固定事件序列）、`/status` 弹出与关闭、权限模态窗的允许/拒绝回传、补全列表出现。运行 `uv run python -m pytest tests -q` 全部通过；运行 `uv run python -m pytest tests/test_import_boundaries.py -q` 通过（确认未破坏依赖边界）。`docs/design-docs/cli-architecture.md` 与 `cli-message-rendering-architecture.md` 已更新为 Textual 架构描述。

整体最终验收：一个对本仓库零了解的人，按本文从头执行，能在 `uv run python -m ui.cli.app` 下完成一次"提问→看到流式回答→触发一次工具→看到工具横幅→`/status` 查看状态→`Esc` 返回→`/exit` 退出"的完整流程。

## Idempotence and Recovery

- 所有步骤可重复运行：`uv sync`、`uv add`、`compileall`、`pytest` 多次执行无副作用。`uv add` 对已存在依赖是幂等的。
- 新增代码集中在 `ui/cli/tui/`，里程碑 1~5 期间旧 `ui/cli/prompt_input/` 与旧入口逻辑保持可用（通过 `git stash`/分支可随时回退到旧 UI）。建议在独立分支实施，每个里程碑一个提交，便于回滚。
- 里程碑 6 的删除是破坏性步骤：删除前确认里程碑 1~5 验收全部通过且新测试覆盖到位；删除后立即运行全量测试。如失败，可 `git revert` 该删除提交而保留前序里程碑成果。
- batch 路径（非 TTY）在整个过程中保持不变，作为"即使 Textual 出问题也能跑通一轮"的安全后备。
- `.env`、`.onecode/` 下的会话与设置文件不被本计划结构性改动；`/connect` 仍只增改 `ONECODE_PROVIDER_ID/MODEL/API_KEY/BASE_URL` 四个键并保留其余行（沿用 `write_provider_env`）。

## Artifacts and Notes

主对话流事件分发的目标骨架（里程碑 2，置于 `ui/cli/tui/app.py`，示意）：

    @work(exclusive=True)
    async def run_agent(self, line: str) -> None:
        log = self.screen.query_one("#message-log", MessageLog)
        preview = self.screen.query_one("#stream-preview", StreamingPreview)
        status = self.screen.query_one("#status-panel", StatusPanel)
        status.set_executing(); self._start_spinner()
        self._stream_buffer = ""; banners = {}
        try:
            attachments = await self.runtime.attachment_collector.collect_for_user_turn(
                line, self.runtime.state, self.runtime.message_store.current_messages(),
                is_main_thread=True)
            async for event in self.runtime.loop.stream(line, attachments=attachments):
                t = event.type
                if t == "assistant_delta":
                    self._stream_buffer += event.text; preview.show_text(self._stream_buffer)
                elif t == "tool_started":
                    cid = event.metadata.get("tool_call_id"); name = event.metadata.get("tool_name") or ""
                    banners.setdefault(cid, ToolBanner(name)).set_running(); preview.show_tool(name)
                elif t == "tool_progress":
                    cid = event.metadata.get("tool_call_id")
                    if cid in banners: banners[cid].set_running()
                elif t == "tool_result" and event.result is not None:
                    cid = getattr(event.result, "tool_call_id", None)
                    summary = render_tool_result_summary(event.result, workspace=self.runtime.workspace)
                    if cid in banners: banners.pop(cid)  # 横幅定格后写摘要
                    log.write(summary)
                elif t == "error":
                    log.write(Text(event.text or "error", style="onecode.error"))
                    self.runtime.error_log_recorder.record_error(...); self.runtime.error_log_recorder.flush()
                elif t == "completed":
                    self._final = event.text
            if self._stream_buffer.strip():
                log.write(Markdown(self._stream_buffer))
        finally:
            self._stop_spinner(); status.set_idle(); preview.clear_preview()

（字段已核实：`assistant_delta` 用 `event.text`；`tool_started`/`tool_progress` 用 `event.metadata["tool_name"]` 与 `event.metadata["tool_call_id"]`；`tool_call_ready` 用 `event.metadata["tool_call"]`；`tool_result` 用 `event.result`；`completed` 用 `event.text`。横幅以 `tool_call_id` 为键。）

权限桥接骨架（里程碑 3，置于 `ui/cli/tui/permission.py`，示意）：

    class TextualPermissionPrompter:
        def __init__(self, app): self._app = app
        async def request_permission(self, request):
            future = asyncio.get_event_loop().create_future()
            self._app.push_screen(PermissionScreen(request),
                                  callback=lambda resp: future.set_result(resp))
            return await future

回车发送/Shift+Enter 换行（里程碑 1，置于 `ui/cli/tui/widgets/prompt_input.py`，示意，参考 `docs/references/ui/coomi/ui/widgets/prompt_text_area.py`）：

    class PromptInput(TextArea):
        class Submitted(Message):
            def __init__(self, text): self.text = text; super().__init__()
        async def _on_key(self, event):
            if event.key == "enter":
                event.stop(); event.prevent_default()
                if self.text.strip(): self.post_message(self.Submitted(self.text.strip()))
            elif event.key == "shift+enter":
                event.stop(); event.prevent_default(); self.insert("\n")
            else:
                await super()._on_key(event)

## Interfaces and Dependencies

新增依赖：`textual>=8.2.7`（运行时），`textual-dev>=1.7.0`（开发调试，dev 组）。不移除 `rich`、`prompt-toolkit`（`prompt-toolkit` 在 `prompt_input` 删除后若无其他引用，里程碑 6 可一并从 `dependencies` 移除——删除前用 `rg "prompt_toolkit|prompt-toolkit"` 确认无残留）。

复用且不得修改其行为的现有接口：

- `ui/cli/app.py` 的 `build_runtime(workspace, *, trust_prompt=None) -> CliRuntime`（新增可选关键字参数，默认行为不变）。
- `ui/cli/types.py` 的 `CliRuntime`（含 `with_session`、`with_model_config`）与 `CommandResult`。
- `ui/cli/commands.py` 的 `dispatch_command(runtime, line) -> CommandResult`、`command_registry()`、`visible_commands()`。
- `ui/cli/resume.py` 的 `list_session_summaries(workspace)`、`restore_runtime_from_target(runtime, target)`、`SessionSummary`、`resolve_resume_target`。
- `ui/cli/connect.py` 的 `write_provider_env(env_path, ProviderEnvUpdate)`、`ProviderEnvUpdate`；以及 `infrastructure.providers.connection.ProviderConnectionService().list_connect_options()` 与 `infrastructure.config.env.normalize_base_url`。
- `ui/cli/renderer.py` 的 `render_status/usage/memory/permissions/skills/tasks/mcp_status/compact/clear/resume/restored_messages/error/group/tool_result_summary/banner`。
- `ui/cli/tool_renderers.py` 的 `render_tool_result`、`render_fallback_tool_result`。
- `core/stream_events.py` 的 `AgentEvent`、`AgentEventType`。
- `services/permissions` 的 `PermissionRequest`、`PermissionResponse`、`PermissionPrompter`（协议）。

里程碑结束时必须存在的新接口：

在 `ui/cli/tui/app.py`：

    class OneCodeApp(App):
        def __init__(self, workspace: Path) -> None: ...
        async def on_mount(self) -> None: ...            # push LoadingScreen + 启动装配 worker
        def run_agent(self, line: str) -> None: ...       # @work(exclusive=True)
        async def dispatch(self, line: str) -> None: ...  # 命令路由 + 多步交互

在 `ui/cli/tui/permission.py`：

    class TextualPermissionPrompter:                      # 实现 PermissionPrompter 协议
        def __init__(self, app: OneCodeApp) -> None: ...
        async def request_permission(self, request: PermissionRequest) -> PermissionResponse: ...

在 `ui/cli/tui/widgets/`：`MessageLog(RichLog)`（启用选择，提供 `get_selected_text()` 或委托内置选择）、`StreamingPreview(Static)`（`show_text/show_thinking/show_tool/clear_preview`）、`StatusPanel(Widget)`（`set_executing/set_idle/set_spinner` + `render()`，显示 OneCode/模型/cwd）、`PromptInput(TextArea)`（`Submitted` 消息）、`ToolBanner`（`set_arguments/set_running/set_done/build()->Table`）、`CommandList(Widget)`（补全列表）。

在 `ui/cli/tui/` 各 Screen：`LoadingScreen(Screen)`、`MainScreen(Screen)`、`PageScreen(ModalScreen)`、`SelectScreen(ModalScreen)`、`ConnectScreen(ModalScreen)`、`PermissionScreen(ModalScreen)`、`TrustScreen(ModalScreen)`。

`ui/cli/app.py` 的 `main(argv=None) -> int`：非 TTY 走 `ui/cli/batch.py` 的 `run_batch(workspace) -> int`；TTY 走 `OneCodeApp(workspace).run()`。

---

修订说明：本文档为初版，依据 2026-06-13 的现状调研与用户三项决策（入口不变、装配移入界面挂载后的后台任务并配启动加载界面、状态栏仅显示 OneCode/模型/cwd）撰写。已核对 `core/loop.py` 中各 `AgentEvent` 的实际字段并回填到 `Context and Orientation`、`Artifacts and Notes` 与主对话流骨架（`tool_started`/`tool_progress` 带 `tool_name`+`tool_call_id`，`tool_call_ready` 带 `tool_call`，`tool_result` 带 `result`，横幅以 `tool_call_id` 为键）。
