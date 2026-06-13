# 增强 CLI 工具结果渲染

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本文遵守仓库根目录 `PLANS.md`。后续实现者修改本计划时，必须让本文继续保持自包含：只阅读当前文件和当前工作区，就能完成实现、验证和恢复。

## Purpose / Big Picture

完成本计划后，OneCode CLI 的默认对话流会在工具执行完成时显示更有语义的结果摘要，而不是只有 `[tool_name call_id ok/error]` 这种低信息量文本。用户在终端中可以直接看出模型读了哪个文件、读了多少行，搜索命中了多少文件或多少行，bash 命令是否成功、退出码和输出规模是什么，文件写入或编辑改了哪个路径以及改动规模。对文件类工具，默认输出必须显示文件相对当前 workspace 的完整相对路径，例如 `ui/cli/renderer.py`，不能显示工具调用 id，也不能只显示 `renderer.py` 这种丢失目录信息的 basename。

本计划刻意不把工具开始、排队、进度或模型刚发起工具调用的事件显示到默认 CLI 主对话区。默认主屏只渲染最终 `tool_result`。工具生命周期事件仍可保留给 trace、测试、未来 debug 页面或更复杂的 TUI，但不进入普通对话输出。这样能改善可读性，同时避免终端输出被短生命周期状态刷屏。

用户可以通过运行 `uv run python -m ui.cli.app` 启动 CLI，发起一个会触发 `read_file`、`grep`、`glob`、`bash`、`write_file` 或 `edit_file` 的任务，并在工具结果返回后看到清晰的 1 到 3 行摘要。自动化验证通过 focused tests 证明每个工具的 `ToolExecutionResult` 会被渲染成预期文本，并证明未知工具仍回退到旧摘要。

## Progress

- [x] (2026-06-12) 阅读 `PLANS.md`，确认 ExecPlan 必须自包含、可执行、包含进度/发现/决策/复盘，并在写入 Markdown 文件时不包裹外层代码块。
- [x] (2026-06-12) 阅读当前 CLI 渲染实现和参考实现，确认 OneCode 当前默认主屏只消费 `assistant_delta` 与最终 `tool_result`，工具结果摘要入口是 `ui/cli/renderer.py` 的 `render_tool_result_summary()`。
- [x] (2026-06-12) 与用户确认产品边界：默认 CLI 不显示 `tool_call_ready`、`tool_started` 或 `tool_progress`，只显示工具调用结果。
- [x] (2026-06-12) 创建本 active ExecPlan，记录只增强 `tool_result` 渲染、不改变默认事件消费范围的方案。
- [x] (2026-06-12) 根据用户反馈修订计划：文件类工具结果必须显示完整相对路径，默认用户输出不显示工具调用 id，因为调用 id 对用户没有价值。
- [x] (2026-06-12) 根据用户反馈修订计划：成功工具结果不显示 `ok`，使用 `[tool] ...`；只有错误结果使用 `[tool error] ...`。
- [x] (2026-06-12) 实现 `ui/cli/tool_renderers.py`，提供工具结果渲染策略注册表和 fallback。
- [x] (2026-06-12) 将 `ui/cli/renderer.py::render_tool_result_summary()` 改为委托工具结果渲染策略，并在 `ui/cli/app.py` 的 `tool_result` 分支传入 `runtime.workspace`。
- [x] (2026-06-12) 为 `read_file`、`grep`、`glob`、`bash`、`write_file`、`edit_file` 增加 condensed 结果渲染。
- [x] (2026-06-12) 增加 focused tests，覆盖成功、错误、未知工具 fallback、文本截断/分页、后台 bash 和文件完整相对路径。
- [x] (2026-06-12) 运行 focused tests、CLI 渲染相关 tests、compile check 和全量测试；`tests/test_cli_tool_renderers.py`、`tests/test_async_cli_streaming.py`、`tests/test_cli_commands.py`、`uv run python -m compileall ui services tools core` 和 `uv run python -m pytest tests -q` 已通过。
- [x] (2026-06-12) 更新 `docs/design-docs/cli-message-rendering-architecture.md` 和 `docs/design-docs/cli-architecture.md`，记录工具结果渲染策略模块。
- [x] (2026-06-12) 实现完成后更新本文 `Outcomes & Retrospective`。

## Surprises & Discoveries

- Observation: 当前 OneCode CLI 已经不是早期“只打印最终文本”的版本，已有 Rich、page mode、权限面板、trace 文件和 streaming assistant delta。
  Evidence: `ui/cli/app.py` 中 `main_loop_async()` 消费 `runtime.loop.stream(...)`；`ui/cli/renderer.py` 使用 Rich `Console`、`Panel`、`Table` 和 `Text`；`ui/cli/views/` 已存在 status、tasks、MCP、memory、permissions 等视图。

- Observation: 默认主对话区目前只对 `assistant_delta` 和 `tool_result` 有用户可见处理，没有显示 `tool_call_ready`、`tool_started` 或 `tool_progress`。
  Evidence: `ui/cli/app.py` 中普通 prompt 分支只在 `event.type == "assistant_delta"` 时流式打印文本，在 `event.type == "tool_result"` 时调用 `renderer.render_tool_result_summary(event.result)`，在 `completed` 时保存最终文本。

- Observation: core/runtime 已经发布工具生命周期事件，但本计划不消费这些事件到默认 CLI。
  Evidence: `core/stream_events.py` 定义了 `tool_call_ready`、`tool_started`、`tool_progress` 和 `tool_result`；`core/loop.py` 会产出这些事件。用户已明确要求 CLI 只显示工具调用结果。

- Observation: 内置工具已经返回足够多的结构化 metadata，可以支撑更好的 UI，而不必解析 stdout 或 content 文本来判断成功失败。
  Evidence: `tools/read_file/tool.py` 返回 `path`、`offset`、`line_count`；`tools/grep/tool.py` 返回 `mode`、`num_files`、`num_matches`、`num_lines`、pagination 信息；`tools/glob/tool.py` 返回 `num_files`、`total_matches_before_pagination`、pagination 信息；`tools/bash/tool.py` 返回 `exit_code`、`duration_ms`、`timed_out`、`stdout_chars`、`stderr_chars`、`command_name`；`tools/write_file/tool.py` 返回 `path`、`operation`、`line_count`、`diff`、`diff_truncated`；`tools/edit_file/tool.py` 返回 `path`、`replacement_count`。

- Observation: 参考实现最值得借鉴的是“框架层分派状态，工具层提供语义化渲染策略”，而不是 React/Ink 消息数组重绘本身。
  Evidence: 参考实现中 `UserToolResultMessage` 负责取消、拒绝、错误、成功分派；`UserToolSuccessMessage` 校验 output schema 后调用工具自己的 `renderToolResultMessage()`；Bash、FileRead、Grep 等工具各自有 `UI.tsx`。

- Observation: OneCode 的 streaming CLI 测试此前没有覆盖 `tool_result` 主屏输出路径。
  Evidence: 新增 `tests/test_async_cli_streaming.py::test_async_cli_renders_only_final_tool_result_with_workspace_path` 后，测试显式发送 `tool_call_ready`、`tool_started`、`tool_progress` 和最终 `tool_result`，断言只有最终 read_file 摘要可见，且输出不包含 `call_read_1`。

- Observation: 专属 renderer 可以通过直接构造 `ToolExecutionResult` 完成验证，不需要运行真实工具 handler 或 provider。
  Evidence: `uv run python -m pytest tests/test_cli_tool_renderers.py -q` 通过，12 个测试覆盖 read_file、grep、glob、bash、write_file、edit_file、错误和 fallback。

## Decision Log

- Decision: 默认 CLI 主对话区只显示最终 `tool_result`，不显示 `tool_call_ready`、`tool_started`、`tool_progress`。
  Rationale: 用户明确认为 CLI 不需要显示工具调用开始等中间事件。OneCode 当前默认主屏以对话可读性为主，工具进度更适合 trace、debug page 或未来 TUI。
  Date/Author: 2026-06-12 / Codex

- Decision: 工具结果渲染策略放在 `ui/cli/`，不加入 `services/tools/types.py::ToolDescriptor`。
  Rationale: `ToolDescriptor` 是 runtime 和模型 schema 的事实来源，位于服务层，不能依赖 Rich 或 CLI 表现。CLI 渲染是 UI 行为，应留在 `ui/cli`。未来如果需要多个 UI 共享渲染事实，可以再抽象 provider-neutral `display_data`，但本计划先保持最小改动。
  Date/Author: 2026-06-12 / Codex

- Decision: 渲染成功或错误状态时，状态来源只能是 `ToolExecutionResult.is_error` 和结构化 `metadata`，不能通过解析 stdout、stderr 或 content 文本来推断。
  Rationale: 参考实现会保留原始 `toolUseResult` 并基于 schema 渲染。OneCode 目前没有 typed output object，但已经有 `metadata`。用 metadata 可以避免 UI 与工具输出文本格式耦合。
  Date/Author: 2026-06-12 / Codex

- Decision: 默认渲染采用 condensed 形式，目标是每个工具结果 1 到 3 行。
  Rationale: CLI 主对话区应帮助用户快速理解工具做了什么，而不是展开完整文件内容、搜索列表、stdout/stderr 或 diff。完整内容仍保留在 transcript、tool result content、result store、trace 或 future page view。
  Date/Author: 2026-06-12 / Codex

- Decision: 未知工具和未覆盖工具继续使用旧的 fallback 摘要。
  Rationale: OneCode 支持 MCP 动态工具、agent、skill、task、background task 等工具。第一阶段只覆盖高频内置工具，fallback 能保证行为稳定，不因新工具缺少 renderer 而失败。
  Date/Author: 2026-06-12 / Codex

- Decision: 文件类工具的默认结果摘要必须显示完整相对路径，不能显示工具调用 id，也不能只显示 basename。
  Rationale: 工具调用 id 是 runtime 内部关联字段，对终端用户几乎没有诊断价值；文件路径才是用户判断 agent 行为是否正确的核心事实。只显示 basename 会在多目录同名文件时制造歧义，因此应基于 workspace 显示完整相对路径。
  Date/Author: 2026-06-12 / Codex

- Decision: 已覆盖工具的成功结果不显示 `ok`，统一使用 `[tool_name] Summary...`；错误结果使用 `[tool_name error] Summary...`。
  Rationale: 成功是默认路径，额外的 `ok` 增加噪音但不提供有效信息。错误状态才需要在 bracket 中突出显示。这个格式更接近用户期望的 `[edit_file] Edited ui/cli/renderer.py with 1 replacement(s)`。
  Date/Author: 2026-06-12 / Codex

- Decision: `glob` 截断摘要使用 `[glob] Found <total> file(s), showing <shown>`，只在 offset 非 0 时追加 offset，不复用 grep 的 `showing first <limit> after offset <offset>` 文案。
  Rationale: glob 的 metadata 已同时提供总数和当前页数量；重复显示 limit 会让默认主屏变啰嗦。保持一行摘要更符合本计划的 condensed 输出目标。
  Date/Author: 2026-06-12 / Codex

## Outcomes & Retrospective

2026-06-12 / Codex: 已完成默认主屏工具结果摘要的代码实现。新增 `ui/cli/tool_renderers.py` 作为 CLI 专属策略注册表，覆盖 `read_file`、`grep`、`glob`、`bash`、`write_file` 和 `edit_file`；`ui/cli/renderer.py::render_tool_result_summary()` 继续作为入口并保留 fallback；`ui/cli/app.py` 在最终 `tool_result` 事件到达时传入 `runtime.workspace`。已覆盖工具的成功输出不显示 `ok` 和 tool call id，文件类工具输出完整 workspace 相对路径。已通过 `uv run python -m pytest tests/test_cli_tool_renderers.py -q`、`uv run python -m pytest tests/test_async_cli_streaming.py -q`、`uv run python -m compileall ui services tools core`、`uv run python -m pytest tests/test_cli_tool_renderers.py tests/test_async_cli_streaming.py tests/test_cli_commands.py -q` 和全量 `uv run python -m pytest tests -q`（389 passed）。尚未做真实 provider 手动 CLI 验证；自动化测试已经覆盖默认主屏只渲染最终 `tool_result` 的路径。

## Context and Orientation

OneCode 是 Python code agent runtime。CLI 位于 `ui/cli/`，是当前用户和 agent runtime 交互的终端界面。CLI 不执行工具 handler，也不决定权限或沙箱策略；它装配 runtime、读取用户输入、调用 `AgentLoop.stream()`，并把 runtime 事件渲染到终端。

本计划涉及几个术语。`tool_result` 指工具执行完成后返回给模型和 CLI 的结果对象，在 OneCode 中对应 `services/tools/types.py::ToolExecutionResult`。这个对象包含 `tool_call_id`、`tool_name`、`content`、`is_error`、`metadata` 和 `followup_messages`。`tool_call_id` 是 runtime 内部用来把模型 tool use 和 tool result 配对的标识，不是面向用户的信息；默认 CLI 不应把它显示给用户，除非进入未覆盖工具的最后 fallback。`metadata` 是结构化字典，保存 UI 和 runtime 可以安全读取的附加事实，例如路径、行数、退出码和是否截断。`condensed` 指默认主屏的简洁摘要，不展开完整内容。

当前默认 CLI 对话流在 `ui/cli/app.py::main_loop_async()`。普通用户输入被提交给 `runtime.loop.stream(line, attachments=attachments)`。循环中，如果事件类型是 `assistant_delta`，CLI 直接流式打印模型文本；如果事件类型是 `tool_result`，CLI 调用 `renderer.render_tool_result_summary(event.result)`；如果事件类型是 `completed`，CLI 保存最终文本。虽然 `core/stream_events.py` 和 `core/loop.py` 已经支持 `tool_call_ready`、`tool_started` 和 `tool_progress`，本计划不改变默认主屏对这些事件的处理。

当前工具结果摘要入口在 `ui/cli/renderer.py::render_tool_result_summary()`。它只读取 `ToolExecutionResult.tool_name`、`tool_call_id` 和 `is_error`，输出类似 `[read_file call_xxx ok]` 或 `[bash call_xxx error]` 的文本。这个输出对用户不够有用，尤其是 `call_xxx`。恢复历史路径 `render_restored_messages()` 和兼容历史视图 `render_history()` 也会折叠工具结果，但本计划优先改默认主屏的实时工具结果摘要。是否复用同一策略到恢复历史可以作为可选收尾，必须避免在恢复历史中展开大结果。

内置工具位于 `tools/`。高频工具及其可用 metadata 如下：

- `tools/read_file/tool.py`：读取文本文件。成功 metadata 包含 `path`、`offset`、`line_count`。
- `tools/grep/tool.py`：用 ripgrep 搜索文件内容。metadata 包含 `mode`、`num_files`、`num_matches`、`num_lines`、`filtered_count`、`applied_limit`、`applied_offset`、`truncated`。
- `tools/glob/tool.py`：按路径模式查找文件。metadata 包含 `num_files`、`total_matches_before_pagination`、`filtered_count`、`applied_limit`、`applied_offset`、`truncated`、`path`。
- `tools/bash/tool.py`：执行 Git Bash 命令。metadata 包含 `exit_code`、`duration_ms`、`timed_out`、`read_only`、`command_count`、`stdout_chars`、`stderr_chars`、`command_name`，后台命令还包含 `task_id`、`task_type`、`status`、`output_file`、`background`。
- `tools/write_file/tool.py`：创建或覆盖文件。metadata 包含 `path`、`operation`、`line_count`、`diff`、`diff_truncated`。
- `tools/edit_file/tool.py`：精确字符串替换。metadata 包含 `path`、`replacement_count`，错误时包含 `error` 和相关路径或匹配数。

参考实现放在 `docs/references/ui` 和 `docs/references/Tools_full`。它的核心模式是：消息组件负责按状态分派，工具本身提供 `renderToolResultMessage()` 和 `renderToolUseErrorMessage()`。OneCode 不复制 React 组件和消息数组重绘，但吸收这个设计思想：框架层统一调用一个策略入口，具体工具渲染由工具名对应的策略函数完成。

## Plan of Work

第一阶段增加 CLI 专属工具结果渲染模块。新建 `ui/cli/tool_renderers.py`。该模块只依赖标准库、Rich 基础类型、`pathlib.Path`、`services.tools.types.ToolExecutionResult` 和 `ui.cli.views.common.display_path`，不能导入具体工具 handler，不能执行文件读取、命令执行或 provider 调用。它提供一个主函数 `render_tool_result(result: ToolExecutionResult, *, workspace: Path) -> str` 或 `-> Text | Group | str`。为了保持 `ui/cli/app.py` 现有打印路径简单，第一版推荐返回纯字符串；如需要颜色，可让 `renderer.print_renderable()` 后续统一处理，但不要在第一版引入复杂 Rich layout。

`tool_renderers.py` 中定义一个 renderer 注册表，键是 `tool_name`，值是函数。函数签名建议为 `Callable[[ToolExecutionResult, Path], str]`，第二个参数是 workspace，用于把绝对路径格式化成完整相对路径。注册表覆盖 `read_file`、`grep`、`glob`、`bash`、`write_file` 和 `edit_file`。主函数先按 `tool_name` 查找，找不到时调用 fallback。已覆盖工具的默认输出不能包含 `tool_call_id`。成功结果格式是 `[tool_name] Summary...`，错误结果格式是 `[tool_name error] Summary...`。未覆盖工具的 fallback 可以保留调用 id 作为最后诊断信息，但应弱化它，例如 `[unknown_tool] call call_xyz` 或 `[unknown_tool error] call call_xyz`；不能让 fallback 约束影响已覆盖工具。

第二阶段把 `ui/cli/renderer.py::render_tool_result_summary()` 改为接受 workspace 并委托 `tool_renderers.render_tool_result()`。`ui/cli/app.py` 在收到 `tool_result` 时应调用 `renderer.render_tool_result_summary(event.result, workspace=runtime.workspace)`。`ui/cli/app.py` 不需要新增对 `tool_call_ready`、`tool_started` 或 `tool_progress` 的处理。这个不改动是产品要求，不是遗漏。`renderer.py` 仍然是 CLI 输出入口，`tool_renderers.py` 是更细的策略模块。

第三阶段实现每个高频工具的 condensed 输出。所有工具渲染都应以一行摘要为主，必要时追加第二行，不展示完整 `content`。对于错误结果，优先显示 `metadata["error"]`；如果没有错误码，再显示 fallback。路径显示必须使用 `ui/cli/views/common.py::display_path(path, workspace)` 或等价逻辑：当路径在 workspace 内时显示完整相对路径，例如 `tools/read_file/tool.py`；当路径不在 workspace 内时显示原始绝对路径。不要只显示 basename，不要显示工具调用 id 来替代路径。

建议输出形态如下。具体文字可以微调，但测试应固定关键事实：

- `read_file` 成功：`[read_file] Read <line_count> line(s) from <relative_path>`。如果有 `offset` 且不是 1，追加 `from line <offset>`。`relative_path` 必须是完整 workspace 相对路径，例如 `ui/cli/renderer.py`。
- `read_file` 错误：`[read_file error] <error_code> <relative_path>`，例如 file_not_found。若 metadata 没有 path，才省略路径。
- `grep` 成功：根据 `metadata["mode"]` 区分。`files_with_matches` 显示 found `<num_files>` file(s)；`count` 显示 found `<num_matches>` match(es) across `<num_files>` file(s)；`content` 显示 found `<num_lines>` line(s) across `<num_files>` file(s)。如果 `truncated` 为 true，追加 `, truncated` 或 `showing first <applied_limit> after offset <applied_offset>`。
- `glob` 成功：显示 found `<total_matches_before_pagination>` file(s)，如果当前页只包含 `num_files` 且被截断，显示 showing `<num_files>`。
- `bash` 成功：显示 `exit <exit_code>`、耗时毫秒、stdout/stderr 字符数。若 `background` 为 true，显示 background task id、status 和 output file。若 `timed_out` 为 true，即使 exit code 存在也必须醒目显示 timed out。
- `write_file` 成功：显示 `Created` 或 `Updated` `<relative_path>`，包含 `<line_count>` line(s)。如果 metadata 有 diff 且 `diff_truncated` 为 true，可以追加 `diff truncated`，但不要展开 diff。
- `edit_file` 成功：显示 `Edited <relative_path> with <replacement_count> replacement(s)`。

第四阶段补充测试。优先新增 `tests/test_cli_tool_renderers.py`，直接构造 `ToolExecutionResult`，调用 `ui.cli.tool_renderers.render_tool_result(result, workspace=tmp_path)`。这些测试不需要真实 provider，不需要启动 CLI，不需要执行工具 handler。覆盖范围至少包括：未知工具 fallback、read_file 成功且显示完整相对路径、grep 三种 mode、glob 截断、bash 成功、bash 错误或 timed out、background bash、write_file create/update 且显示完整相对路径、edit_file 成功且显示完整相对路径，以及错误 metadata 的渲染。再更新现有 `tests/test_async_cli_streaming.py` 或相关 CLI streaming 测试，确认主循环仍只在 `tool_result` 事件上打印工具摘要，不要求显示 started/progress，并确认主循环把 `runtime.workspace` 传给 renderer。

第五阶段更新文档。实现完成后，更新 `docs/design-docs/cli-message-rendering-architecture.md` 中“工具调用渲染流”段落：说明默认主屏仍只显示最终 `tool_result`，但结果摘要已经由 `ui/cli/tool_renderers.py` 按工具名策略渲染。明确 `tool_call_ready`、`tool_started` 和 `tool_progress` 仍存在但默认不显示。必要时更新 `docs/design-docs/cli-architecture.md` 的文件职责表，加入 `tool_renderers.py`。

## Concrete Steps

所有命令都在仓库根目录运行：

    cd D:\study\OneCode

开始实现前查看工作区，确认已有改动并避免覆盖用户文件：

    git status --short

预期当前工作区可能已经有其他未提交改动。本计划的实现者只能编辑本计划涉及的文件，不能还原或清理无关改动。

新增文件：

    ui/cli/tool_renderers.py

在该文件中定义：

    from collections.abc import Callable
    from pathlib import Path
    from services.tools.types import ToolExecutionResult

    ToolResultRenderer = Callable[[ToolExecutionResult, Path], str]

    def render_tool_result(result: ToolExecutionResult, *, workspace: Path) -> str:
        ...

    def render_fallback_tool_result(result: ToolExecutionResult) -> str:
        ...

具体 helper 函数应保持小而纯：只从 `result` 和 `result.metadata` 读取字段，格式化字符串并返回。不要调用文件系统，不要解析 JSON content 来决定状态，不要 import `tools.*` 目录中的具体实现。

修改 `ui/cli/renderer.py`：

    from ui.cli.tool_renderers import render_tool_result

    def render_tool_result_summary(result: Any, *, workspace: Path | None = None) -> str:
        if isinstance(result, ToolExecutionResult) and workspace is not None:
            return render_tool_result(result, workspace=workspace)
        return legacy fallback for duck-typed tests

如果现有测试传入 fake object 而不是 `ToolExecutionResult`，可以让 `renderer.py` 保留兼容 fallback。选择最小改动，避免为了类型纯净破坏已有测试。正常 CLI 路径必须传入 `runtime.workspace`，否则文件类工具无法稳定显示完整相对路径。

新增测试文件：

    tests/test_cli_tool_renderers.py

测试示例应直接构造：

    ToolExecutionResult(
        tool_call_id="call_1",
        tool_name="read_file",
        content="1\tline",
        metadata={"path": "D:\\study\\OneCode\\ui\\cli\\app.py", "offset": 1, "line_count": 42},
    )

断言渲染结果包含 `[read_file]`、`42` 和 `ui\\cli\\app.py` 或 `ui/cli/app.py`。测试必须证明成功输出不包含 `ok`，输出不是 `app.py` 这种 basename，也不包含 `call_1`。

运行 focused tests：

    uv run python -m pytest tests/test_cli_tool_renderers.py -q

如果更新了 streaming 测试，再运行：

    uv run python -m pytest tests/test_async_cli_streaming.py -q

运行编译检查：

    uv run python -m compileall ui services tools core

最后运行相关 CLI 测试，必要时运行全量测试：

    uv run python -m pytest tests/test_cli_tool_renderers.py tests/test_async_cli_streaming.py tests/test_cli_commands.py -q
    uv run python -m pytest tests -q

手动验证需要可用 `.env` provider。如果没有真实 provider，可以跳过手动 CLI 运行，并在最终说明中明确未做真实 provider 验证。若有可用 provider，运行：

    uv run python -m ui.cli.app

在 CLI 中请求一个会触发读取当前文件的任务，例如：

    read ui/cli/renderer.py and summarize what render_tool_result_summary does

预期观察：assistant 文本流式输出仍照常；工具执行完成后，默认主屏不显示 started/progress 行，只显示一个更清晰的 read_file 结果摘要，例如：

    [read_file] Read 120 line(s) from ui/cli/renderer.py

再请求搜索：

    search for render_tool_result_summary in ui/cli

预期 grep 结果摘要包含命中文件数或行数，而不是只有 `[grep call_xxx ok]`。预期 read/write/edit 这类文件工具结果包含完整相对路径，而不是工具调用 id。

## Validation and Acceptance

验收标准一：默认 CLI 主对话区仍只在 `tool_result` 到达后显示工具相关文本。`tool_call_ready`、`tool_started` 和 `tool_progress` 不应新增默认可见输出。可以通过阅读 `ui/cli/app.py::main_loop_async()` 和运行 streaming 测试确认。

验收标准二：`renderer.render_tool_result_summary()` 对 `read_file`、`grep`、`glob`、`bash`、`write_file`、`edit_file` 返回工具语义摘要。测试应证明摘要包含工具名、成功或错误状态、关键 metadata 数值和路径或任务 id。对文件类工具，路径必须是完整 workspace 相对路径，不能是 basename，不能是 tool call id。

验收标准三：未知工具和未覆盖工具仍有 fallback，不抛异常，不破坏 MCP 或未来插件工具。fallback 可以包含 tool name、error 状态和 call id，但 call id 只允许出现在这种未知工具兜底路径中；已覆盖工具不能显示 call id。成功 fallback 不需要显示 `ok`。

验收标准四：已覆盖工具的成功结果使用 `[tool_name] Summary...` 格式，不显示 `ok`；已覆盖工具的错误结果使用 `[tool_name error] Summary...` 格式。

验收标准五：渲染函数不读取文件、不执行命令、不调用工具 handler、不访问 provider。它们只消费传入的 `ToolExecutionResult`。

验收标准六：错误结果使用 `ToolExecutionResult.is_error` 和 `metadata["error"]` 判断显示，不通过解析 stdout/stderr/content 来推断是否失败。

验收标准七：输出保持 condensed。默认主屏不能展开完整文件内容、完整 grep 文件列表、大 stdout/stderr 或完整 diff。若某个工具内容很长，摘要仍保持 1 到 3 行。

验收标准八：以下命令通过：

    uv run python -m pytest tests/test_cli_tool_renderers.py -q
    uv run python -m pytest tests/test_async_cli_streaming.py -q
    uv run python -m compileall ui services tools core

如果全量测试时间可接受，也应通过：

    uv run python -m pytest tests -q

## Idempotence and Recovery

本计划是 additive-first 的安全改动。新增 `ui/cli/tool_renderers.py` 和 `tests/test_cli_tool_renderers.py` 可以重复创建和修改，不会写入 `.onecode/` session 数据，不会改动 provider 配置，不会执行外部命令。修改 `renderer.render_tool_result_summary()` 时保留 fallback，可以降低对现有测试和动态工具的影响。

如果某个专属 renderer 出错，主函数应捕获本地格式化异常并返回 fallback，而不是让 CLI 主循环崩溃。实现可以使用小 helper，例如 `_safe_render(renderer, result)`。这不是吞掉工具执行错误；工具执行错误仍由 `result.is_error` 表达。这里防护的是 UI 格式化 bug。

如果测试发现路径显示在 Windows 和 POSIX 上不稳定，不要退回到 basename。应使用 `Path(path).relative_to(workspace)` 或 `display_path(path, workspace)`，并在测试里接受 `\` 与 `/` 两种分隔符。完整相对路径是本计划的核心验收要求。

如果后来决定把恢复历史也改成同一策略，必须先保证恢复出的 message 能可靠重建 `ToolExecutionResult` 或等价对象。不要在恢复历史里解析大 content 或展开外置 result store；恢复历史继续保持折叠摘要。

## Artifacts and Notes

当前默认工具结果输出示例：

    [read_file call_abc ok]
    [grep call_def ok]
    [bash call_ghi error]

目标默认工具结果输出示例：

    [read_file] Read 82 line(s) from ui/cli/renderer.py
    [grep] Found 6 matches across 2 files
    [glob] Found 31 files, showing 10
    [bash error] exit 1 in 142 ms, stdout 0 chars, stderr 230 chars
    [write_file] Updated docs/design-docs/example.md (24 line(s), diff truncated)
    [edit_file] Edited ui/cli/renderer.py with 1 replacement(s)

未知工具 fallback 示例，call id 只在未知工具兜底中保留：

    [mcp__server__tool] call call_xyz

参考实现的设计模式总结：参考 UI 将工具结果按取消、拒绝、错误、成功分派；成功路径读取原始 tool result，经 output schema 校验后调用工具专属渲染函数。OneCode 本计划吸收“工具专属渲染函数”这一点，但不采用 React/Ink 消息数组重绘，也不把渲染函数放进 runtime ToolDescriptor。

## Interfaces and Dependencies

新增模块 `ui/cli/tool_renderers.py` 必须提供以下接口：

    from collections.abc import Callable
    from typing import Any

    from services.tools.types import ToolExecutionResult

    ToolResultRenderer = Callable[[ToolExecutionResult], str]

    def render_tool_result(result: ToolExecutionResult, *, workspace: Path) -> str:
        """Return the default CLI text for a completed tool result."""

    def render_fallback_tool_result(result: Any) -> str:
        """Return the legacy fallback summary for unknown tools."""

建议内部函数：

    def _render_read_file(result: ToolExecutionResult, workspace: Path) -> str: ...
    def _render_grep(result: ToolExecutionResult, workspace: Path) -> str: ...
    def _render_glob(result: ToolExecutionResult, workspace: Path) -> str: ...
    def _render_bash(result: ToolExecutionResult, workspace: Path) -> str: ...
    def _render_write_file(result: ToolExecutionResult, workspace: Path) -> str: ...
    def _render_edit_file(result: ToolExecutionResult, workspace: Path) -> str: ...

建议注册表：

    RENDERERS: dict[str, ToolResultRenderer] = {
        "read_file": _render_read_file,
        "grep": _render_grep,
        "glob": _render_glob,
        "bash": _render_bash,
        "write_file": _render_write_file,
        "edit_file": _render_edit_file,
    }

`ui/cli/renderer.py::render_tool_result_summary(result: Any, *, workspace: Path | None = None) -> str` 继续作为外部入口存在，以减少调用方改动。它应委托 `ui.cli.tool_renderers.render_tool_result(result, workspace=workspace)`。`workspace` 为空时只能走兼容 fallback；正常 CLI 主循环必须传入 `runtime.workspace`。它还应保留对非 `ToolExecutionResult` fake object 的兼容。`ui/cli/app.py` 不新增工具 started/progress 分支。

禁止依赖：

- 不要在 `services/tools/types.py::ToolDescriptor` 增加 Rich 或 CLI 渲染字段。
- 不要让 `services/tools/` import `ui/cli`。
- 不要在 renderer 中 import `tools/read_file/tool.py` 等具体工具实现。
- 不要调用文件系统或 shell 来生成 UI 摘要。

2026-06-12 / Codex: 初始计划创建。原因：用户要求基于 OneCode 当前 CLI 渲染与参考实现的对比，撰写中文 ExecPlan；用户进一步明确默认 CLI 不需要显示工具调用开始、排队或进度，只需要显示工具调用结果。

2026-06-12 / Codex: 修订路径与调用 id 规则。原因：用户指出阅读文件等结果应显示文件完整相对路径，工具调用 id 对用户没有价值；计划因此改为把 workspace 传入工具结果 renderer，并要求已覆盖工具的默认输出不显示 call id。

2026-06-12 / Codex: 修订成功状态格式。原因：用户指出默认工具成功不需要加 `ok`，期望格式类似 `[edit_file] Edited ui/cli/renderer.py with 1 replacement(s)`；计划因此要求成功态省略 `ok`，错误态保留 `[tool error]`。

2026-06-12 / Codex: 实施工具结果渲染计划。原因：用户要求开始执行本计划编写代码；本次变更新增 CLI 专属 `tool_renderers.py`、接入 `renderer.py` 与 `app.py`、补充 focused tests，并更新 CLI 渲染设计文档以记录实际结构。
