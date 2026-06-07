# 轻量终端 UI 重构计划草稿

本文是讨论用的轻量草稿，不按 `PLANS.md` 完整 ExecPlan 模板展开。目标是整理 OneCode CLI 的用户可见 slash 命令、引入富文本终端 UI，并补齐当前底层能力已经支撑的若干命令。

## 背景判断

OneCode 当前 CLI 已经有真实 runtime 装配、streaming 输出、权限提示、MCP、任务、后台任务、trace、compaction、memory、skills 和 transcript/session 能力。下一步 UI 重构不应把更多 runtime 逻辑放进 `ui/cli/`，而应让 CLI 只消费已有 service 状态和事件。

终端 UI 形态建议保持增强 REPL，而不是全屏 TUI：

- 用 `rich` 渲染 banner、状态、表格、工具事件、错误、权限提示和 Markdown。
- 用 `prompt_toolkit` 接管输入、历史、命令补全和命令预览。
- 暂不引入 `textual`。除非后续明确需要常驻多面板、trace explorer、任务看板或全屏布局。

## 可参考的 UI 设计经验

Claude Code 的 UI 分析里，最值得参考的是产品层的信息架构和交互组织方式，而不是它的自定义 React-to-terminal 渲染引擎。OneCode 应参考“命令、视图、主题、输入体验如何分层”，但实现上继续走 Python CLI 的轻量路线。

可以直接参考：

- 命令作为结构化对象：slash command 不应继续散落在 `if/elif` 分支、帮助文本和命令预览里。OneCode 应建立 `CommandSpec` 注册表，让命令执行、命令预览、banner、测试共用同一份事实来源。
- App / REPL / Screen / Component 的职责分离：OneCode 可用 Python 模块对应这层边界，例如 `app.py` 负责启动和 runtime 装配，`input.py` 负责 prompt_toolkit 输入，`commands.py` 负责命令注册和分发，`renderer.py` 负责 Rich 渲染，`views/` 负责 status、tasks、memory、permissions 等专门视图。
- 设计系统思想：统一命令、路径、成功、错误、权限、模型、session、任务状态的颜色和文本样式。OneCode 不需要 React ThemeProvider，但需要一个轻量主题表或 `CliTheme`。
- 命令对应专门视图：`/status`、`/tasks`、`/memory`、`/permissions` 这类命令不应只拼接临时字符串，而应有稳定的 render function 或 view class，便于测试和复用。
- 输入处理体验：命令补全、输入历史、命令说明预览、后续 `@file` 补全都值得参考。实现上用 `prompt_toolkit` 的 completer、history、`display_meta` 和 `patch_stdout()`。

可以改造后参考：

- 主题系统：不照搬 ThemeProvider，只做 `CliTheme` 或常量表，集中定义 Rich style 名称和颜色。
- 屏幕/视图概念：不做全屏 page 切换，先做“一次性输出后回到 REPL”的轻量视图，例如 `StatusView`、`TasksView`、`MemoryView`、`PermissionsView`。
- Markdown 与 diff 渲染：可以使用 Rich 内置 Markdown、Syntax、Panel 或 Table 能力，后续为文件编辑结果补统一 diff panel；不自建 terminal markdown renderer。
- 状态 Provider 思想：Claude Code 用 React context 管 UI 状态；OneCode 已有 `CliRuntime`，应继续把它作为 UI 可见状态容器，不另造全局 UI store。
- 组件目录组织：可以后续新增 `ui/cli/views/`、`ui/cli/theme.py`、`ui/cli/input.py`，但不要为了形式拆成大量小组件。

不参考的部分作为边界约束：

- 不实现自定义 Ink、React reconciler、终端 DOM、Yoga/Flexbox 布局、screen buffer、双缓冲 diff、鼠标事件、alt-screen 或动画帧。
- 不为了 UI 重构引入一个新的渲染引擎项目。
- 不把 Claude Code 的 200+ 组件规模当成 OneCode 当前阶段目标。

## 命令整理原则

用户可见命令必须服务日常使用。纯开发者诊断、重复别名、内部细节展示不放在默认 slash 命令里。

命令注册应改为结构化 `CommandSpec`，让命令执行、命令预览、帮助展示和测试共用同一份事实来源。

```python
@dataclass(frozen=True)
class CommandSpec:
    name: str
    args: str
    description: str
    category: str
    visible: bool
    handler: Callable[[CliRuntime, list[str]], CommandResult]
```

`visible=False` 只用于内部或测试入口；本轮明确要求删除的命令不应改成隐藏保留，而应移除用户命令分支。

## 必须删除或合并的命令

以下命令从用户可见 slash 命令中移除，并同步删除帮助、banner、命令预览和测试中的用户可见预期。

| 命令 | 处理 |
|:---|:---|
| `/quit` | 删除。只保留 `/exit`。 |
| `/help` | 删除。命令说明由输入时命令预览承担，不再作为用户命令。 |
| `/tools` | 删除。工具列表属于开发者诊断，不向普通用户暴露。 |
| `/trace` | 删除。trace 属于开发者诊断，保留底层 trace 文件和 recorder，不提供用户命令。 |
| `/background-tasks` | 删除。后台任务并入 `/tasks` 展示。 |
| `/mcp tools` | 删除特殊参数。`/mcp` 只展示 MCP 概览；如需工具细节由开发者看配置或 trace。 |

注意：删除命令不等于删除底层能力。`TraceRecorder`、`ToolRegistry`、`BackgroundTaskManager`、MCP tool descriptors 继续保留给 runtime、测试和开发者使用。

## 保留的现有用户命令

第一轮保留这些已有命令：

- `/status`：运行状态总览。
- `/tasks`：任务视图，合并 durable tasks 与 background task 状态。
- `/mcp`：MCP server 状态概览，不再支持 `tools` 子参数。
- `/history`：会话消息摘要。
- `/compact`：手动压缩上下文。
- `/resume`：恢复已有 session。
- `/clear`：开始新 session。
- `/exit`：退出。

## 建议较快新增的命令

这些命令对应的底层能力已经基本存在，适合在 UI 重构中逐步补齐。

| 命令 | 用途 | 主要数据来源 | 优先级 |
|:---|:---|:---|:---|
| `/skills` | 列出可用 skills 摘要 | `LoaderSkillCatalogProvider` / skill catalog | 高 |
| `/memory` | 查看 session memory 与 long-term memory 状态，可显示路径和最近更新时间 | `SessionMemoryStore`、`LongTermMemoryStore`、state metadata | 高 |
| `/permissions` | 查看当前 session/project permission 规则摘要 | `PermissionPolicy`、`SessionPermissionStore`、`ProjectPermissionSettingsStore` | 高 |
| `/hooks` | 查看已注册 hook 事件和数量 | `HookRegistry` | 中 |
| `/usage` | 从 `/status` 拆出 token、turn、compaction usage | `RuntimeState.usage`、metadata | 中 |
| `/context` | 显示上下文治理状态、最近 compact、session memory、result store 摘要 | `ContextCompactionService`、state metadata | 中 |
| `/export` | 导出当前 session transcript 到文件或 stdout 摘要 | `MessageStore`、`JsonlTranscriptStore` | 中 |
| `/recap` | 基于当前会话生成一句 recap | `AgentLoop` 或轻量 model call，需要避免污染主消息链 | 低 |

第一批建议只做 `/skills`、`/memory`、`/permissions`、`/usage`。它们都是只读状态命令，风险低，适合验证新的 `CommandSpec` 和 Rich 渲染。

## `/tasks` 合并方向

`/tasks` 应成为用户看到“正在发生什么”的单一入口：

- Durable task list：来自 `TaskStore`。
- Background jobs：来自 `BackgroundTaskManager.list_tasks()`。
- Dream / local agent / local bash 的状态、输出文件、失败原因作为任务视图中的一个分区。

删除 `render_background_tasks()` 的独立命令入口，但可以保留 renderer 内部 helper，供 `render_tasks()` 组合输出。

## `/mcp` 简化方向

`/mcp` 只展示：

- server 名称
- transport
- connected / failed / disabled 状态
- tool count
- instructions 是否存在
- error 摘要

删除 `show_tools` 参数和 `/mcp tools` 分支。MCP 工具明细不作为普通用户命令显示。

## 富文本 UI 改造步骤

1. 新增命令注册表。
   - 把 `handle_command()` 的 if/elif 分支迁移到 `CommandSpec` registry。
   - renderer/banner/preview 从 registry 读取可见命令。
   - 删除 `/quit`、`/help`、`/tools`、`/trace`、`/background-tasks` 和 `/mcp tools`。

2. 引入 `rich`。
   - `renderer.py` 逐步从字符串返回改为 Rich renderable 或 `Console` 输出。
   - 先改 banner、status、tasks、permissions、memory、errors。
   - assistant streaming 仍保持简洁文本流，避免 Rich Live 过早干扰输入。

3. 引入 `prompt_toolkit`。
   - 用 `PromptSession.prompt_async()` 替换 `asyncio.to_thread(input, ...)`。
   - 使用 completer 提供 slash 命令补全。
   - 使用 `display_meta` 实现命令预览，替代 `/help`。
   - 使用 `patch_stdout()` 避免 streaming 输出打乱输入行。

4. 增加只读状态命令。
   - `/skills`
   - `/memory`
   - `/permissions`
   - `/usage`

5. 合并任务视图。
   - `/tasks` 同时显示 durable tasks 和 background jobs。
   - 删除 `/background-tasks` 测试预期，补充 `/tasks` 合并视图测试。

6. 收敛文档和测试。
   - 更新 `docs/design-docs/cli-architecture.md`。
   - 更新 CLI command tests。
   - 若删除命令影响旧测试，测试应跟随新用户界面，不保留兼容别名。

## 代码触点

- `pyproject.toml`：新增 `rich`、`prompt-toolkit`。
- `ui/cli/commands.py`：命令注册表、删除命令、实现新增只读命令。
- `ui/cli/renderer.py`：Rich 渲染、任务合并、删除 help/tools/trace 用户渲染入口。
- `ui/cli/app.py`：prompt_toolkit 输入循环、命令预览、stdout patch。
- `ui/cli/permissions.py`：后续可用 Rich 美化权限提示，但不改变 permission policy。
- `tests/test_cli_commands.py`：更新命令存在性、删除行为、合并行为。
- `tests/test_async_cli_streaming.py`：确认 prompt_toolkit 接入后 streaming 不回退。

## 验收草案

- 输入 `/` 时出现命令预览，不需要 `/help`。
- `/quit`、`/help`、`/tools`、`/trace`、`/background-tasks` 均不再是有效用户命令。
- `/mcp tools` 不再有特殊行为；`/mcp` 是唯一 MCP 用户命令。
- `/tasks` 同时展示 durable tasks 和 background jobs。
- `/skills`、`/memory`、`/permissions`、`/usage` 至少提供只读摘要。
- `AgentLoop`、tool executor、permission policy、MCP manager、trace recorder 的 runtime 边界不因 UI 重构改变。
- 通过 focused CLI tests 与 compile check。

## 暂不做

- 不做全屏 TUI。
- 不做 plugin runtime。
- 不做 `/branch`、`/rewind`、`/goal`、`/plan` 这类需要新状态模型的命令。
- 不做 provider 参数切换类命令，如 `/fast`、`/effort`、`/model`。
- 不把 trace/tool registry/MCP tool 明细作为普通用户命令暴露。
