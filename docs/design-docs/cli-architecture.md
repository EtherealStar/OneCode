# CLI Architecture

本文描述 `ui/cli/` 的架构。CLI 是 OneCode 当前的增强 REPL 界面，负责应用装配、交互输入、命令处理、附件收集、权限提示和终端渲染，但不实现 agent 主循环、工具执行、安全策略或 provider 协议。

启动：`uv run python -m ui.cli.app`（TTY 时启动内联终端 REPL；stdin 非 TTY 时走 batch 路径）。

TTY 路径采用 **内联终端渲染模型**（与 Claude Code / Ink 的 Static + dynamic 分层同类，基于 `prompt_toolkit` + Rich）：定稿内容打印进终端正常缓冲区（继承终端明暗背景、可向上滚动回看）；底部输入框、流式预览、斜杠补全画在可擦除的动态区；`/status`、`/resume` 等临时界面进入备用屏幕（DEC 1049），退出后主屏幕恢复且临时内容不进入 scrollback。

## 文件职责

| 文件 | 职责 |
|:---|:---|
| `app.py` | `build_runtime()` 依赖装配、`main()` 入口分流、MCP trust/skip 处理、长期记忆 dream 钩子 |
| `batch.py` | 非交互 batch：读 stdin 一行、`loop.stream()` 流式打印到 stdout |
| `input.py` | `read_batch_line()`、fallback `read_confirm_sync()`（MCP trust / batch 权限） |
| `terminal/` | 内联终端 REPL：`InlineRepl` 主循环、静态/动态区渲染、备用屏幕查看页和临时确认界面（见下表） |
| `commands.py` | `CommandSpec` 注册表、slash command 解析与 `dispatch_command()` 分发 |
| `suggestions.py` | `/` 命令、`/resume` 参数、`@file` 内联补全数据 |
| `resume.py` | session summary 扫描、标题派生、transcript target 解析和恢复 helper |
| `connect.py` | `write_provider_env()`、provider 选项列举 |
| `theme.py` | Rich style 名称（前景色，永不设背景）、light/dark 主题选择、Unicode 状态符号 |
| `renderer.py` | Rich renderable 工厂、batch 路径 `print_renderable()` |
| `tool_renderers.py` | 工具结果 1 行摘要 |
| `views/` | 用户可见 Rich 状态视图 |
| `permissions.py` | 权限请求内容格式化与 batch fallback 三选项 prompter |
| `types.py` | `CliRuntime`、`CommandResult` |

### `terminal/` 子模块

| 文件 | 职责 |
|:---|:---|
| `repl.py` | `InlineRepl` 主循环：装配、读输入、dispatch、`run_agent`、shutdown |
| `detect.py` | 终端背景明暗探测（OSC 11 → COLORFGBG → dark） |
| `static_output.py` | 静态区打印：反色用户行、`onecode>` 前缀、工具横幅/结果、未信任 MCP 提示 |
| `prompt_session.py` | 动态区输入框：上下边框、`/`/`@` 补全菜单、Enter/Tab 语义（空闲态提交） |
| `completer.py` | `suggestions_for` → prompt_toolkit `Completer` 适配 |
| `queue.py` | 运行中输入队列（FIFO of `QueuedInput`，区分 prompt / slash） |
| `stream_session.py` | 流式动态区：live Markdown 预览（ANSI 节流重绘）、Esc 取消、**运行中输入框**（同 Application 内底部 Buffer，Enter 入队）、通过 `output_coordinator.py` 调度静态区写入 |
| `stream_state.py` | turn 内 UI 状态模型 `CliStreamUiState`（streaming_text / tools / pending_static_commits / stream_mode） |
| `stream_reducer.py` | 纯函数 `reduce_stream_event`，事件 → state；无 I/O |
| `stream_view.py` | `render_stream_body_ansi` / `render_status_fragments` 把 state 翻译成 prompt_toolkit 可显示文本 |
| `output_coordinator.py` | `TerminalOutputCoordinator` — 流式会话里唯一允许写静态区的组件 |
| `transient.py` | DEC 1049 备用屏幕生命周期 + `can_enter_alternate_screen` 能力守卫 |
| `page.py` | 备用屏幕分页查看 renderable（`/status` 等），Esc 返回 |
| `selector.py` | 备用屏幕列表选择（`/resume`） |
| `connect_flow.py` | `/connect` 多步向导（备用屏幕） |
| `permission_prompt.py` | TTY 权限确认（非全屏、可擦除的三选项临时面板） |
| `trust_prompt.py` | MCP trust 启动期确认 |

## 入口分流

- **TTY**：`main()` 先构建 `CliRuntime`，使用 `mcp_trust_mode="prompt"` 在启动期询问未信任项目 stdio MCP server 的信任（通过 `trust_prompt` 回调），再运行 `InlineRepl(runtime).run()`。`InlineRepl` 打印 banner，提示被跳过的 MCP server，进入主循环。
- **非 TTY**：`batch.run_batch(workspace)`，不启动 prompt_toolkit；MCP trust 与权限 fallback 使用 stdin `read_confirm_sync()`。

## 内联布局（`terminal/`）

- **静态区**（终端 scrollback，`static_output.py`）：banner、反色 `>` 用户行、`onecode>` 助手前缀 + Markdown 定稿、工具横幅与结果摘要。用绑定 `sys.stdout` 的 Rich `Console` 打印，**不设 background**，背景由终端宿主提供。
- **动态区**（`prompt_session.py` / `stream_session.py`）：非全屏 `prompt_toolkit.Application(full_screen=False, erase_when_done=True)`。空闲时是带上下 `─` 边框的输入框（`PromptSession`）；agent 运行时是同一个 prompt_toolkit 应用承载的 live Markdown 流式预览 + 状态行 + 底部 running input box（`StreamingSession`）。阶段结束时动态区自擦除，不污染 scrollback。
- **备用屏幕**（`transient.py` 等）：全屏临时界面用 `prompt_toolkit` `full_screen=True`（其自身管理 DEC 1049）。`transient.py` 暴露 `can_enter_alternate_screen()` 作为 TTY 能力守卫，以及直接渲染 Rich 时可用的 `transient_terminal_scope()` 上下文。

## 接口设计

### build_runtime

```python
build_runtime(
    workspace,
    *,
    trust_prompt: Callable[[McpTrustPromptRequest], "trust"|"skip"] | None = None,
    permission_prompter: PermissionPrompter | None = None,
    mcp_trust_mode: Literal["prompt", "skip"] = "prompt",
) -> CliRuntime
```

`mcp_trust_mode="prompt"` 时，未信任项目 stdio MCP server 使用 `trust_prompt` 或 stdout + `read_confirm_sync()` 询问用户。内联 TTY 路径使用 `mcp_trust_mode="prompt"`（不再像旧路径那样默认 skip）。未信任 server 摘要写入 `RuntimeState.metadata["mcp_untrusted_servers"]`，随后由 `McpConnectionManager` fail closed 标记为 `untrusted`。batch 路径仍可显式传 `mcp_trust_mode="skip"`。

### 主对话流

`InlineRepl._run_turn()` 把 `runtime.loop.stream()` 事件交给 `stream_session.StreamingSession`，由其拥有动态区预览与 Esc 取消。事件流路径：

```mermaid
flowchart LR
  Loop["AgentLoop.stream()"] --> Session["StreamingSession"]
  Session --> Coalescer["StreamingCoalescer"]
  Coalescer --> Reducer["reduce_stream_event (pure)"]
  Reducer --> State["CliStreamUiState"]
  State --> View["render_stream_body_ansi / render_status_fragments"]
  State --> Coord["TerminalOutputCoordinator (queue only)"]
  Coord --> Static["static_output.print_tool_result / print_assistant_markdown"]
  View --> App["prompt_toolkit dynamic app"]
```

| 事件 | UI 行为 |
|:---|:---|
| `assistant_delta` | 累加到 `state.streaming_text` → 动态区 live Markdown 预览（50ms 节流，ANSI 渲染）。reducer 强制要求事件 metadata 携带稳定的 `assistant_call_id` 和 `model_turn_index`，缺失则进入 error 状态。 |
| `assistant_message_completed` | reducer 立即把当前 `streaming_text` 打包成 `StaticCommit(assistant_markdown)` 入队，然后清空 `streaming_text`。动态区不再保留已定稿文本，新的 assistant 文本从空区开始。 |
| `tool_call_ready` / `tool_started` / `tool_progress` | reducer 维护 `state.tools`（queued / running），记录 `tool_call_id → assistant_call_id` 和 `tool_call_id → declared_index` 映射；view 在 body 显示 `tool: <name>` 列表；状态行显示 `tool: <name>` 或 `tools: N running`（**不会**显示裸 `thinking…`） |
| `tool_result` | reducer 把 `ToolExecutionResult` 暂存到 `completed_tool_results_by_assistant[assistant_call_id][declared_index]`，然后通过 `release_ready_tool_result_commits` 只释放"同一 assistant_call_id 下从最小未提交 index 开始连续完成"的结果到 `state.pending_static_commits`；`StreamingSession._commit_pending_to_coordinator` 转交给 `TerminalOutputCoordinator`，coordinator 在事件循环内立即 `flush_ready_checkpoints` 写入静态区。**不再**等 turn 结束。 |
| `completed` | reducer 翻 `turn_completed`；如果 `streaming_text` 仍有残留（例如 provider 没发 `assistant_message_completed`），兜底 commit 一次并清空。已完成 commit 不会重复打印。 |
| `error` | reducer 写入 `state.error_text`，view 在 body 尾部显示；coordinator 不再为 error 打印额外块。 |

完成后动态区擦除，最终 Markdown 留在 scrollback。Esc 设置取消标志、退出预览 app，coordinator 通过 `queue_status_line` 打印「已取消」。

### Checkpoint 提交模型（execplan §M1/§M2/§M3/§M4）

`static_output.print_*` 仍然是**唯一**允许写入静态区的入口。reducer / view / `StreamingSession` 都只能 stage 状态；它们**不**直接调用 `print_tool_result` 或 `print_assistant_markdown`。`TerminalOutputCoordinator` 是流式会话里**唯一**允许把这些状态写入静态区的组件：

- `queue_commit(commit, *, workspace=None)` 只把 `StaticCommit` 追加到内部队列，**不**写 stdout。
- `flush_ready_checkpoints()` 是 async 提交边界。`StreamingSession` 在事件循环内每次 `apply_event` 后 await 它（不再等 turn 结束）；当 dynamic app 仍在运行时，coordinator 通过 `prompt_toolkit.run_in_terminal` 临时挂起动态区再写静态 scrollback，避免 Rich 静态输出覆盖输入框。

每条 `StaticCommit` 携带稳定的 `assistant_call_id`（由 `core/stream_events.py::mint_assistant_call_id` 派生）和 `model_turn_index`，作为 assistant message → tool call → tool result 的 UI 归属回链。reducer 在 `tool_result` 时按**声明顺序**（`declared_index`）释放，不允许"后声明但先完成"的工具越过前面的工具。

权限确认不走事件流，由 `permission_prompt.TtyPermissionPrompter` 在工具执行链中 `await request_permission()`。TTY 权限请求使用非全屏、`erase_when_done=True` 的临时三选项面板；当流式预览 app 正在运行时，权限提示用 `prompt_toolkit.run_in_terminal` 临时挂起预览并以同样三选项的纯 confirm 询问，避免嵌套 prompt_toolkit app。

### Command Registry

`dispatch_command(runtime, line) -> CommandResult` 行为不变。`InlineRepl` 层处理：

- `presentation="page"` → `terminal.page.TransientPage`（备用屏幕，Esc 返回）
- `presentation="inline"` → 静态区 `Console.print`
- `replay_messages` 非空 → `terminal.transcript_replay.replay_messages_to_static`（恢复成功后在主 scrollback 中按正常静态输出函数重放历史）
- `interaction="resume_selector"` → `terminal.selector.TransientSelector` 选中后 `restore_runtime_from_target`，返回 inline 恢复通知 + `replay_messages`（不再展示恢复历史 page）
- `interaction="connect"` → `terminal.connect_flow.run_connect_flow` + `write_provider_env` + `with_model_config`
- `should_exit` → flush + 退出循环

## 核心数据流

```mermaid
flowchart TD
  Entry["main() TTY path"] --> Build["build_runtime(mcp_trust_mode=prompt)"]
  Build --> Repl["InlineRepl(runtime).run()"]
  Repl --> Banner["静态区 banner + 未信任 MCP 提示"]
  Repl --> Prompt["PromptSession.read (动态区)"]
  Prompt --> Cmd{以 / 开头?}
  Cmd -->|是| Dispatch["dispatch_command"]
  Dispatch --> Modal{"interaction / presentation"}
  Modal -->|page| Page["TransientPage (备用屏幕)"]
  Modal -->|resume| Select["TransientSelector (备用屏幕)"]
  Modal -->|connect| Connect["run_connect_flow (备用屏幕)"]
  Modal -->|inline| Log["静态区 Console.print"]
  Cmd -->|否| Agent["_run_turn"]
  Agent --> Stream["loop.stream → StreamingSession"]
  Stream --> Preview["动态区 live Markdown + 静态区工具横幅"]
  Stream --> Perm["TtyPermissionPrompter"]
```

## 关键机制

### 终端主题

`detect.detect_terminal_brightness()` 探测宿主明暗（OSC 11 查询 → `COLORFGBG` → 暗色回退）。`theme.rich_theme_for(brightness)` 选择 light/dark Rich 主题；两份主题都只定义前景色，**永不设 background**，背景始终由终端提供。反色用户行在暗色用 `white on black`、亮色用 `black on white`。

### 补全与输入语义

`suggestions.py` 的 `suggestions_for(runtime, text, cursor)` 经 `completer.InlineCompleter` 接入 prompt_toolkit。菜单打开时：↑↓ 移动选中项；**Enter 采纳并提交**选中项（无选中则提交字面文本）；**Tab 仅将选中项填入输入框、不提交**。运行中输入框（`StreamingSession` 动态区底部）通过 buffer 的 `accept_handler` 把 Enter 翻译成 `InputQueue.push`，因此 agent 输出期间继续输入不会被吞掉、也不会打断当前 turn。

**输入归口**：

- **空闲态提交** 归 `PromptSession`：它只读用户输入、发出 `SubmissionKind.SUBMIT` / `CANCEL` / `EXIT`，不触碰 `InputQueue`。
- **运行中提交** 归 `StreamingSession`：底部 input box 共享同一个 `InputQueue`；`accept_handler` 入队后清空 buffer。
- **队列 drain** 归 `InlineRepl._drain_queue`：当前 turn 结束后按 FIFO 弹出 `QueuedInput`，`kind == "slash"` 的走 `_handle_command`（不进 agent），`kind == "prompt"` 的走 `_run_turn`。
- 动态区 `view` 渲染 `queued_inputs` 快照，列出最多 N 条可见命令并折叠 overflow 摘要；这些行只在动态区显示，永远不写进静态 scrollback。

### Connect

启动时 `main()` 尝试 `build_runtime()`，若 `.env` 缺必要配置抛出 `ProviderError`，则改为 `build_unconfigured_runtime()` 创建精简 runtime（`configured=False`）。REPL 主循环在 `configured=False` 时拦截所有非 `/connect`、`/exit` 输入，提示用户使用 `/connect` 配置供应商。

`/connect` 由 `connect_flow.run_connect_flow` 多步向导完成（全程备用屏幕）：

1. **选择 Provider**：`TransientSelector` 列出 catalog 中所有供应商（含 Ollama 和 Custom）。
2. **Custom → 输入 Base URL**：`requires_base_url` 为 `True` 时先收集 URL。
3. **Key 处理**：检查 `.env` 中该 provider 是否已有 API key。
   - 有 → K/R/C 三选项（Keep 保留 / Replace 替换 / Cancel 取消）。
   - 无 + `api_key_required` → 输入新 key。
   - 无 + `not api_key_required`（Ollama）→ 跳过。
4. **拉取模型列表**：`fetch_models_for_connect` 自动探测端点。Ollama 用 `/api/tags`；其他 provider 优先试 `{base_url}/v1/models`，再试 `{base_url}/models`。失败时 fallback 到手动输入模型名 + `test_model_connection` 连接测试。
5. **模型选择器**：`TransientSelector` 展示模型列表。
6. **保存**：`write_provider_env()` 更新 `ONECODE_*` 键 → `with_model_config()` 重建模型客户端（`configured` 变为 `True`）。

### 权限

TTY：`TtyPermissionPrompter` 使用可擦除临时面板，只消费 `PermissionRequest.options` 中的 allow once、allow session、deny 三项。Esc 和 Ctrl-C 返回 deny。流式预览运行中用 `run_in_terminal` 纯 confirm 回退。非 TTY / batch：`render_permission_panel` + stdin `read_confirm_sync`，也只接受 once/session/deny。权限请求 prompt 不写项目规则，不生成 `projectSettings` update。

项目级 allow/deny/ask 规则只通过 `/permissions add|remove|replace allow|deny|ask <rule>` 修改；`/permissions` 无参数仍进入备用屏幕只读查看页。备用屏幕继续用于 `/status`、`/permissions` 等查看页，不用于运行时权限请求。

### 错误处理

`_run_turn` 异常写 `source=cli_main_loop`；退出时 flush transcript/trace/errors 并关闭 MCP。

## 当前限制

batch 路径仍为单行 stdin、纯文本 stdout，无 Markdown 渲染。动态区 live Markdown 预览有界高度（仅显示尾部若干行），完整内容在轮结束时定稿到静态区。流式过程中真正的按键级 Esc 取消依赖动态区预览 app 持有输入焦点。尚缺更细粒度 provider recovery UI。
