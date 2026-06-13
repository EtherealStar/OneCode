# 终端 UI 页面、恢复与连接草稿

本 ExecPlan 是一份活文档。随着工作推进，必须持续更新 `Progress`、`Surprises & Discoveries`、`Decision Log` 和 `Outcomes & Retrospective` 这些章节。

本文档遵循仓库根目录的 `PLANS.md`。这是下一轮 CLI UI 重构的新轻量草稿，不替代也不编辑任何更早的 UI ExecPlan。


## 目的与整体图景 (Purpose / Big Picture)

本节说明目的与整体图景。

OneCode 当前有一个轻量终端 REPL。REPL 的意思是程序等待用户输入一行，执行它，打印输出，然后继续等待下一行。下一步 UI 工作是把这个 REPL 做成一个受控的产品界面，而不是一组打印文本命令。完成后，启动界面只显示具体 workspace 路径和模型名；slash 命令会显示有用的预览和补全；状态类命令会打开页面，阻断普通输入直到用户按 `Esc`；`/resume` 允许用户选择之前的会话并查看它的历史；`/connect` 允许用户在终端里配置模型供应商。

用户可见结果很容易验证。启动 `uv run python -m ui.cli.app`，应看到只包含 `OneCode`、workspace 路径和模型名的紧凑 banner。输入 `/`，应看到带描述的命令建议。执行 `/status`、`/usage`、`/mcp`、`/tasks`、`/permissions`、`/memory` 或 `/skills`，应确认页面会忽略普通文字输入，长内容支持滚动键，并且只有按 `Esc` 才退出。执行 `/resume`，用方向键选择会话，按 `Enter`，应看到该会话先被恢复，然后打开历史页面。执行 `/connect`，选择 DeepSeek 之类的供应商，输入凭据和模型名，应确认下一次请求使用 `.env` 中的新 provider 配置。


## 进度 (Progress)

本节记录进度。

- [x] (2026-06-09, Codex) 基于 UI 讨论创建这个新的 ExecPlan 草稿，未编辑之前的 UI 计划。
- [x] (2026-06-09, Codex) 实现 page-mode 命令结果，以及阻断普通 prompt 输入的页面输入循环。
- [x] (2026-06-09, Codex) 将启动 banner 简化为具体值：产品名、workspace 路径和模型名。
- [x] (2026-06-09, Codex) 修复交互式 prompt 输入里的 Backspace 行为。
- [x] (2026-06-09, Codex) 改进 `/` 和部分输入命令的 slash 命令预览与补全。
- [x] (2026-06-09, Codex) 用 `/resume` 选择器和恢复后的历史页面替代 `/history`。
- [x] (2026-06-09, Codex) 添加 `/connect` 供应商选择、凭据输入、`.env` 写入和运行时模型重绑定。
- [x] (2026-06-09, Codex) 更新测试和 `docs/design-docs/cli-architecture.md`。


## 意外与发现 (Surprises & Discoveries)

本节记录意外发现。

- 观察：transcript store 当前没有稳定的 session title 字段。
  证据：`services/context/transcript.py` 记录的消息行包含 `type`、`uuid`、`parent_uuid`、`session_id`、`timestamp`、`cwd` 和 `message`，但没有 title。在真正的 session metadata 文件出现之前，`/resume` 选择器必须从第一条有用的用户消息里派生一个小标题。

- 观察：provider 配置不能只靠供应商和 API key 完成。
  证据：`infrastructure/config/env.py` 要求 `ONECODE_PROVIDER_ID`、`ONECODE_MODEL` 和 `ONECODE_API_KEY`；带有 `requires_base_url=True` 的供应商，包括 `custom`，还要求 `ONECODE_BASE_URL`。

- 观察：对 `/connect` 来说，只写 `.env` 还不够。
  证据：`ui/cli/app.py` 在 runtime 构建时根据 provider 配置创建 `model_client`、`RelevantMemorySelector`、`ContextEngine`、`SubagentRunner`、抽取服务和 `AgentLoop`。执行 `/connect` 后，当前 `CliRuntime` 必须重新绑定这些依赖模型的对象，否则下一轮仍会使用旧 model client。

- 观察：当前状态类命令是“打印后返回”的命令，不是 modal 页面。
  证据：`ui/cli/commands.py` 返回 `CommandResult(renderable=...)`，`ui/cli/app.py` 打印 renderable 后立刻回到 prompt。当前没有能捕获 `Esc` 或阻断普通文字输入的页面状态。

- 观察：prompt-toolkit 会把 `backspace` key binding 规范化为 `c-h`。
  证据：`tests/test_cli_pages.py::test_prompt_key_bindings_include_backspace_variants` 需要检查规范化后的 key value；实现仍同时注册 `backspace`、`c-h` 和 `delete`。


## 决策记录 (Decision Log)

本节记录设计决策。

- 决策：OneCode 保持轻量 REPL，不改成全屏终端 UI。
  理由：需要的行为可以围绕现有 Rich 和 prompt-toolkit 层添加 modal 页面状态与受控输入循环来实现。用 Ink、React、Textual 或 alternate-screen 应用替换渲染器，会是比当前需求更大的架构变更。
  日期/作者：2026-06-09 / Codex

- 决策：状态类命令打开 page mode。
  理由：`/status`、`/usage`、`/mcp`、`/tasks`、`/permissions`、`/memory` 和 `/skills` 都是只读查看视图。它们可见时，不应把任意用户文字接受为新命令或 prompt。
  日期/作者：2026-06-09 / Codex

- 决策：page mode 只用 `Esc` 退出，滚动键可以浏览长内容。
  理由：用户明确要求页面视图阻断命令输入，只允许 `Esc` 和滚动。`Enter` 和可打印字符必须在 page mode 中被忽略。
  日期/作者：2026-06-09 / Codex

- 决策：启动 banner 显示不带标签的具体值。
  理由：用户希望主页显示模型和 workspace，但不要显示 `workspace`、`model`、`provider` 或 `session` 这类解释标签。主页不能显示模型来源。
  日期/作者：2026-06-09 / Codex

- 决策：`/history` 不属于用户命令集。
  理由：历史查看属于 `/resume`：用户选择一个会话，OneCode 恢复它，然后显示恢复后的会话历史。保留单独的 `/history` 命令会重复导航模型。
  日期/作者：2026-06-09 / Codex

- 决策：`/resume` 是选择器，而不是主要依赖文本参数的命令。
  理由：用户希望每个会话显示小标题，用方向键选择，并按 `Enter` 恢复。直接参数支持可以作为隐藏或兼容路径保留，但可见交互应是选择器。
  日期/作者：2026-06-09 / Codex

- 决策：`/connect` 是交互式 provider 设置向导。
  理由：连接 provider 需要多个值，而且不应把 API key 暴露在命令历史中。页面式向导可以收集 provider、必要时的 base URL、API key 和模型名，而不把这些输入当成普通 prompt。
  日期/作者：2026-06-09 / Codex


## 结果与回顾 (Outcomes & Retrospective)

本节记录结果与回顾。

本轮实现已完成计划中的主要 CLI 行为：`CommandResult` 支持 inline/page presentation 和 resume/connect interaction；状态类命令进入 page mode；启动 banner 只显示 `OneCode`、workspace 路径和模型名；普通 prompt 使用 prompt-toolkit 并补充 Backspace/Delete key binding；`/history` 从用户命令和补全中移除；`/resume` 无参数打开 session selector，恢复后显示历史 page；`/connect` 可选择 provider、收集凭据、写 `.env` 并重绑定当前 runtime 的模型相关对象。

已通过聚焦验证：

    uv run python -m pytest tests/test_cli_commands.py tests/test_cli_completion.py tests/test_cli_resume.py tests/test_cli_pages.py tests/test_cli_connect.py -q

后续仍可补充真实终端手动验收：启动 `uv run python -m ui.cli.app` 后实际按 `/`、`/status`、`/resume` 和 `/connect` 检查终端显示与键盘行为。


## 背景与定位 (Context and Orientation)

本节说明背景和代码定位。

OneCode 是一个 Python code agent runtime。agent 主循环位于 `core/loop.py`，模型 provider 适配器位于 `infrastructure/providers/`，provider 配置由 `infrastructure/config/env.py` 从项目 `.env` 读取，终端用户界面位于 `ui/cli/`。

CLI 入口是 `ui/cli/app.py`。它用 `build_runtime(workspace)` 构建 `CliRuntime`，然后运行 `main_loop_async(runtime)`。当前主循环读取一行输入，通过 `ui/cli/commands.py` 分发 slash 命令，或把普通 prompt 发送给 `AgentLoop.stream()`。

现有 CLI 类型容器是 `ui/cli/types.py::CliRuntime`。它保存当前 `RuntimeState`、`MessageStore`、`ToolRegistry`、`AgentLoop`、provider 标签、model client、tool executor、权限 store、memory 服务、MCP manager、task store 和 background task manager。`/clear` 和 `/resume` 已经依赖 `CliRuntime.with_session()` 来重建 session-scoped 组件。

当前命令注册表位于 `ui/cli/commands.py`。slash 命令是以 `/` 开头的一行，例如 `/status` 或 `/resume`。`CommandSpec` 是命令 metadata 对象，应继续作为命令名、描述、参数提示、可见性、handler 和参数补全的唯一事实来源。

当前渲染器是 `ui/cli/renderer.py` 以及 `ui/cli/views/` 下的视图模块。它们使用 Rich。Rich 是 Python 终端渲染库，可以打印带样式的 panel 和 table。输入层是 `ui/cli/input.py`，使用 prompt-toolkit。prompt-toolkit 是 Python 终端输入库，支持可编辑输入、历史和补全。

参考 UI 文件位于 `docs/references/ui/`。它们展示了有用的产品思路，尤其是命令建议、modal slash-command 页面和 prompt 输入分离。不要复制参考终端渲染器或 React 架构。只借鉴行为：页面可以拥有输入，普通 prompt 可以被隐藏或禁用，命令建议应该出现在输入附近，而不是作为单独 help 命令。


## 工作计划 (Plan of Work)

本节说明工作计划。

第一步，引入一个小型 CLI 交互状态模型。在 `ui/cli/types.py::CommandResult` 中加入页面呈现模式，例如一个取值为 `inline` 和 `page` 的 `presentation` 字段，或使用一个单独的页面结果对象。这个类型必须留在 CLI 层；不要让 `core/` 或 `services/` import Rich 或 prompt-toolkit。更新 `ui/cli/commands.py`，让只读查看命令返回页面呈现结果。应成为页面命令的是 `/status`、`/usage`、`/mcp`、`/tasks`、`/permissions`、`/memory` 和 `/skills`。`/compact`、`/clear` 和 `/exit` 保持 inline action 命令。

第二步，实现页面输入处理。创建一个新模块，例如 `ui/cli/pages.py`。页面是一个临时视图，会渲染内容，并在用户离开前拥有键盘输入。它应该渲染 Rich 对象，然后等待按键。`Esc` 退出。如果内容高于终端，`Up`、`Down`、`PageUp` 和 `PageDown` 负责滚动。`Enter`、普通字母、数字、斜杠和所有其他可打印输入都被忽略。如果 prompt-toolkit 能干净地提供 key loop，就使用 prompt-toolkit。否则在 CLI 层实现一个小型跨平台 key reader，并围绕 key dispatch 逻辑写测试，避免只依赖真实终端行为作为证明。

第三步，简化启动 banner。编辑 `ui/cli/views/status.py::render_banner()`，让它只显示产品名、具体 workspace 路径和具体模型名。它不能显示 `workspace` 或 `model` 这类标签；不能显示 `provider_label`；不能显示 session id。无颜色文本渲染应类似：

    OneCode
    D:\study\OneCode
    gpt-5-codex

第四步，修复普通 prompt 中的 Backspace。检查 `ui/cli/input.py::prompt_async()`。当前 fallback 会在 `stdin` 或 `stdout` 任意一方不是 TTY 时使用普通 `input()`。这个 fallback 对测试有用，但在 desktop app 终端中可能不正确。把输入后端选择做成显式、可测试的逻辑。在真实交互使用中优先使用 prompt-toolkit。如果 prompt-toolkit 在失败环境中没有正确处理 Backspace，则为常见终端 Backspace 形式添加 key binding：`backspace`、`c-h` 和 `delete`。继续为 pytest 和管道保留非交互 fallback。

第五步，改进命令建议。命令 metadata 继续放在 `CommandSpec` 中。用户输入 `/` 时，建议列表应包含所有可见命令。用户输入 `/con` 时，建议列表应过滤出 `/connect`。每个建议都应包含命令名、可选参数提示和描述。`/help` 不能作为可见命令返回。截图中的行为是两列命令列表：左侧命令，右侧描述；第一版先使用 prompt-toolkit 的 display metadata，只有内置菜单无法展示所需预览时，再添加自定义 renderer。

第六步，用 `/resume` 选择器替代 `/history`。从可见命令和补全中移除 `/history`。无参数 `/resume` 应打开 session 选择页面。扫描 `.onecode/*/messages.jsonl`，读取足够的 JSONL record 来统计消息数、查找 timestamp，并派生标题。标题是第一条有用的非空 user message：把空白和换行归一为空格，并截断到大约 60 个字符。如果没有 user message，就用第一条 assistant 文本。如果仍没有文本，就使用 session id。选择器支持 `Esc` 取消，`Enter` 恢复选中 session，`Up` 和 `Down` 移动选择，长列表支持页面滚动键。

第七步，`/resume` 恢复会话后，把该会话历史显示为只读页面。这个页面在 runtime 替换后读取 `runtime.message_store.current_messages()`。它按时间顺序展示消息。用户和 assistant 消息显示短预览。工具结果折叠成类似 `read_file call_123 ok` 或 `bash call_456 error` 的一行，不展开完整 stdout。附件显示类型和短路径或摘要。按 `Esc` 退出历史页面，并回到已恢复会话的 prompt。

第八步，添加 `/connect`。在 `ui/cli/commands.py` 注册它，并把它实现为交互式页面流程，而不是普通 prompt。使用 `infrastructure/providers/connection.py::ProviderConnectionService` 列出 provider 选项。可见 provider 顺序应匹配 `infrastructure/providers/catalog.py::CONNECT_PROVIDER_ORDER`，并让 `custom` 位于最后。第一个界面允许用户用 `Up` 和 `Down` 选择，`Enter` 确认，`Esc` 取消。对于不需要自定义 base URL 的 provider，提示输入 API key 和模型名。对于 `custom`，先提示 base URL，再提示 API key，再提示模型名。`claude-openai-compatible` 这样的 provider 也有 `requires_base_url=True`，所以即使名字不是 `custom`，也应走 base URL 路径。

第九步，从 `/connect` 安全写入 `.env`。在 CLI 或 infrastructure 边界模块中添加 helper，例如 `ui/cli/connect.py` 或 `infrastructure/config/env_writer.py`。它应更新项目根目录 `.env`，尽量保留无关行和注释。它必须设置 `ONECODE_PROVIDER_ID`、`ONECODE_MODEL`、`ONECODE_API_KEY`，并在需要时设置 `ONECODE_BASE_URL`。如果选择的 provider 不需要 base URL，就移除或清空旧的 `ONECODE_BASE_URL`，避免旧 custom endpoint 污染新 provider。绝不能打印 API key，不能把它写入 trace attributes，也不能把它放进错误信息。

第十步，`/connect` 后重新绑定 runtime 模型依赖。在 `ui/cli/types.py` 中添加类似 `CliRuntime.with_model_config()` 的方法，或在 `ui/cli/app.py` 中添加 helper，避免 connect 命令复制整个 `build_runtime()` 函数。这个方法必须用 `create_model_client(workspace / ".env")` 创建新的 model client，更新 `provider_label` 和 `model`，重建持有旧 client 的模型相关服务，并返回替换后的 `CliRuntime`。模型相关组件包括 `RelevantMemorySelector`、`ContextEngine`、`SubagentRunner`、session 和 long-term memory extraction 服务，以及 `AgentLoop`。复用现有 registry、permissions、hooks、task store、background task manager、transcript store 和 message store。

第十一步，更新测试和文档。为 banner 文本、命令可见性、页面结果分类、resume session 标题提取、resume 选择器选择行为、connect `.env` 写入和 runtime 模型重绑定添加 CLI 单元测试。更新 `docs/design-docs/cli-architecture.md`，说明 page mode、`/resume` 选择器和 `/connect`。


## 具体步骤 (Concrete Steps)

本节说明具体步骤。

从仓库根目录 `D:\study\OneCode` 开始工作。

先检查工作树：

    git status --short

工作树中可能已经有无关 UI 改动。不要回滚它们。只编辑本计划需要的文件。

新增或修改 CLI 类型：

    ui/cli/types.py

给 `CommandResult` 添加页面呈现字段，或添加一个 `main_loop_async()` 能检查的等价结果类型。这个结果必须能在没有真实终端的情况下被测试。

添加页面处理：

    ui/cli/pages.py
    ui/cli/app.py

当命令结果要求页面呈现时，`main_loop_async()` 应调用页面循环。如果命令也返回替换 runtime，那么在展示任何依赖恢复后或连接后状态的页面之前，先应用 runtime 替换。

更新命令注册：

    ui/cli/commands.py

适当标记 `/status`、`/usage`、`/mcp`、`/tasks`、`/permissions`、`/memory`、`/skills`、`/resume` 和 `/connect`。从可见命令中移除 `/history`。只有在不干扰选择器的前提下，才保留直接 `/resume <target>` 作为可选兼容路径。

添加 resume 支持：

    ui/cli/resume.py
    ui/cli/views/resume.py

实现 `SessionSummary` 和扫描 `.onecode` sessions 的 helper。scanner 要能容忍损坏的 JSONL：跳过坏 record，并仍然展示任何可加载的 session。

添加 connect 支持：

    ui/cli/connect.py
    ui/cli/views/connect.py
    ui/cli/types.py

实现 provider 选择、凭据收集、`.env` 更新和 runtime 重绑定。

添加测试后，先运行聚焦测试：

    uv run python -m pytest tests/test_cli_commands.py tests/test_cli_completion.py tests/test_cli_resume.py -q

如果单独文件更清楚，可以添加新测试文件，例如：

    tests/test_cli_pages.py
    tests/test_cli_connect.py

然后运行：

    uv run python -m compileall ui infrastructure
    uv run python -m pytest tests/test_import_boundaries.py -q

手动验证时启动 CLI：

    uv run python -m ui.cli.app

尝试这些交互：

    /
    /status
    /resume
    /connect

预期观察结果在下一节说明。


## 验证与验收 (Validation and Acceptance)

本节说明验证方式和验收标准。

启动界面如果显示 `OneCode`、具体 workspace 路径和具体模型名，并且不显示标签、session id、provider/source 名、trace path 或 error log path，则通过验收。

slash 命令补全如果在输入 `/` 时显示带描述的可见命令，输入 `/con` 时提供 `/connect`，且 `/help` 不是可见命令，则通过验收。

page mode 如果让 `/status`、`/usage`、`/mcp`、`/tasks`、`/permissions`、`/memory` 和 `/skills` 打开不接受普通文本或 `Enter` 作为 prompt 输入的页面，则通过验收。按 `Esc` 会退出页面。长页面中，`Up`、`Down`、`PageUp` 和 `PageDown` 只滚动，不提交命令。

Backspace 如果在真实交互运行中允许用户输入 `abc`，按 Backspace，然后提交 `ab`，并且不显示字面控制字符，也不会无法编辑 prompt，则通过验收。围绕新增的 key binding 或后端选择逻辑添加自动化测试。

`/resume` 如果打开带小标题的 session 列表，允许用户用方向键移动，按 `Enter` 恢复选中 session，然后打开该恢复 session 的历史页面，则通过验收。从历史页面按 `Esc` 后，下一次 prompt 必须在恢复后的 session 中运行。`/history` 不能出现在命令补全中。

`/connect` 如果打开 provider 列表，以 catalog 顺序显示内置 provider 且 `custom` 在最后，只对需要 base URL 的 provider 提示 base URL，用隐藏或掩码输入提示 API key，提示模型名，写入 `.env`，并返回一个主页 banner 和下一次模型调用都会使用新模型的 runtime，则通过验收。API key 不得出现在终端输出、测试快照、trace record 或错误文本中。

自动化验证应包括：

    uv run python -m pytest tests/test_cli_commands.py tests/test_cli_completion.py tests/test_cli_resume.py tests/test_cli_pages.py tests/test_cli_connect.py -q
    uv run python -m compileall ui infrastructure
    uv run python -m pytest tests/test_import_boundaries.py -q

如果 `tests/test_cli_pages.py` 或 `tests/test_cli_connect.py` 不是单独文件，那么在标记相关 progress 完成前，必须把覆盖 page 和 connect 行为的具体测试名记录到本计划中。


## 幂等性与恢复 (Idempotence and Recovery)

本节说明幂等性和失败恢复。

所有实现步骤都应是增量且可重复的。重复运行测试和 compile check 不应改变工作树。

`/resume` 选择器绝不能删除 session。如果恢复选中 transcript 失败，显示简短错误，并保持当前 runtime 不变。

`/connect` 流程会写 `.env`，这是用户配置。为了安全，应通过 helper 写入，保留无关设置，并尽量使用临时文件加原子替换。如果新 provider 配置写入后校验失败，显示错误并保持旧 runtime 活跃。不要静默丢弃用户输入的值；应告诉用户需要修复什么。

如果 `.env` 不存在，`/connect` 可以创建它。如果 `.env` 已存在且包含注释或无关 key，应保留它们。如果 API key 输入错误，重复运行 `/connect` 应只更新 provider 字段，并保留无关 key。

不要移动、删除或编辑更早的 ExecPlan 文件。本计划有意作为一个新草稿存在。


## 产物与备注 (Artifacts and Notes)

本节记录示例产物和备注。

无颜色启动输出示例：

    OneCode
    D:\study\OneCode
    gpt-5-codex

`/resume` 选择器示例：

    Resume

    › Fix CLI Backspace and modal pages        2026-06-09  18 messages
      Discuss new UI direction                 2026-06-08  42 messages
      Permission policy cleanup                2026-06-07  15 messages

    Esc cancel    Enter resume    Up/Down move

恢复后的历史页面示例：

    Session History

    User
    docs\references\ui 你要参考的文件放在这个文件夹中...

    Assistant
    我先按仓库约定读一遍架构和相关参考资料...

    Tool
    read_file call_read ok

    Esc return

`/connect` provider 页面示例：

    Connect

    › OpenAI
      DeepSeek
      GLM
      MiniMax
      SiliconFlow
      Gemini
      Claude OpenAI-compatible
      Custom

    Esc cancel    Enter select    Up/Down move

连接成功输出示例，不包含 API key：

    Connected
    DeepSeek
    deepseek-chat


## 接口与依赖 (Interfaces and Dependencies)

本节说明接口和依赖。

Rich 只能用于 CLI 渲染层。Rich 对象可以出现在 `ui/cli/renderer.py` 和 `ui/cli/views/*`，但 `core/` 和 `services/` 不能依赖 Rich。

真实交互终端中，普通 prompt 输入和补全使用 prompt-toolkit。为非交互测试和管道保留普通 fallback。

在 `ui/cli/types.py` 中，命令结果必须支持页面呈现。一个可接受的形状是：

    @dataclass(frozen=True)
    class CommandResult:
        should_exit: bool = False
        runtime: CliRuntime | None = None
        renderable: object | None = None
        presentation: Literal["inline", "page"] = "inline"

在 `ui/cli/pages.py` 中，提供可测试的页面原语。具体名称可以调整，但最终设计必须暴露一种用 renderable 和 key handler 运行页面的方式：

    @dataclass(frozen=True)
    class PageKey:
        name: str

    async def show_page(renderable: object, *, title: str | None = None) -> None:
        ...

对于 `/resume`，在 `ui/cli/resume.py` 中定义 session summary 类型：

    @dataclass(frozen=True)
    class SessionSummary:
        session_id: str
        messages_path: Path
        title: str
        message_count: int
        updated_at: datetime | None

    def list_session_summaries(workspace: Path) -> tuple[SessionSummary, ...]:
        ...

对于 `/connect`，使用 `infrastructure/providers/connection.py::ProviderConnectionService` 获取 provider 选项。添加安全 `.env` writer，位置可以是 `ui/cli/connect.py` 或 `infrastructure/config/env_writer.py`。writer 应有一个狭窄接口，例如：

    @dataclass(frozen=True)
    class ProviderEnvUpdate:
        provider_id: str
        model: str
        api_key: str
        base_url: str | None = None

    def write_provider_env(env_path: Path, update: ProviderEnvUpdate) -> None:
        ...

添加 runtime 重绑定方法或 helper，避免复制 `build_runtime()`：

    def with_model_config(self) -> CliRuntime:
        ...

这个 helper 应重建模型相关对象，并保留 session、transcript、permissions、tasks、MCP manager、hooks 和 background tasks。


## 修订说明 (Revision Note)

本节记录修订说明。

2026-06-09 / Codex: 根据用户要求创建这个新的轻量 ExecPlan 草稿。它包含已讨论的 modal page 行为、Backspace 修复、简化主页、命令预览、带历史查看的 `/resume` 选择器，以及 `/connect` provider 向导。它有意不编辑任何更早的 ExecPlan 文件。

2026-06-09 / Codex: 将草稿正文翻译为中文，保留路径、命令、接口示例和 ExecPlan 结构，便于后续中文实现讨论。

2026-06-09 / Codex: 完成首轮实现并更新测试与 CLI 架构文档。主要产物包括 `ui/cli/pages.py`、`ui/cli/resume.py`、`ui/cli/connect.py`、`ui/cli/views/resume.py`、`ui/cli/views/connect.py`，以及 `CliRuntime.with_model_config()`。
