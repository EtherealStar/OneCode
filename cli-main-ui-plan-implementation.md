# CLI Main UI Plan 实现解析

## 概述

`docs/exec-plans/completed/cli-main-ui-plan.md` 是 OneCode 的第一个 CLI 交互主界面的执行计划。该计划于 2026-06-04 完成，其目标是最小化、无额外依赖、标准库 CLI REPL。本文档逐层解释该计划如何在代码中落地。

---

## 1. 计划的核心要求

计划明确了以下边界条件：

- **单行输入**：使用标准库 `input()`，不引入 Rich/Textual/prompt-toolkit
- **固定工具范围**：第一版只暴露 `read_file` 和 `edit_file`
- **无权限交互**：guard 的 deny/ask 只返回结构化错误，不弹出用户确认
- **JSONL 恢复**：`/resume` 可从现有 transcript 恢复会话并继续对话
- **无测试/离线模式**：不提供 `--dry-run` 等产品入口
- **启动入口**：`uv run python -m ui.cli.app`

---

## 2. 目录结构 —— 计划 vs 实际

计划要求创建以下文件：

```
ui/__init__.py           # ✓ 已创建
ui/cli/__init__.py       # ✓ 已创建
ui/cli/renderer.py       # ✓ 已创建（纯文本渲染函数）
ui/cli/commands.py       # ✓ 已创建（slash command 解析和处理）
ui/cli/app.py            # ✓ 已创建（启动、装配 runtime、主循环）
```

实际实现还新增了一个中立类型模块：

```
ui/cli/types.py          # 避免 app.py 和 commands.py 循环导入
                         # 存放 CliRuntime 和 CommandResult
```

这在计划的 "Interfaces and Dependencies" 章节中已明确预见：*"If importing `CliRuntime` from `app.py` creates a cycle, define a small `Protocol` in `commands.py` ... or move `CliRuntime` into a neutral `ui/cli/types.py`."*

---

## 3. 模块职责分解

### 3.1 `ui/cli/types.py` —— 共享数据类型

```python
@dataclass
class CliRuntime:
    workspace: Path          # 当前工作目录
    state: RuntimeState      # 主循环状态（session_id, turn_count, usage 等）
    message_store: MessageStore  # 消息存储
    registry: ToolRegistry   # 工具注册表
    loop: AgentLoop          # 主循环
    provider_label: str      # provider 显示名
    model: str               # 模型名
    # ... 额外字段（权限、trace 等）

@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False
    runtime: CliRuntime | None = None  # 允许命令替换整个 runtime
```

`CommandResult.runtime` 字段是 `/resume` 和 `/clear` 的关键设计：这些命令需要以新的 `MessageStore` 重建 `AgentLoop`，通过返回替换后的 `CliRuntime` 让主循环无缝切换。

### 3.2 `ui/cli/renderer.py` —— 纯文本渲染

渲染函数只做 `print()` 输出，不持有 runtime。核心函数：

| 函数 | 职责 |
|---|---|
| `render_banner(runtime)` | 启动 banner：workspace、session id、model、命令列表 |
| `render_help()` | 显示所有 slash commands |
| `render_tools(descriptors)` | 列出工具名 + description |
| `render_status(runtime)` | workspace、session、provider、model、turns、usage、transcript 路径 |
| `render_history(messages)` | 按 role 分类显示消息摘要，工具结果显示错误标记 |
| `render_assistant(text)` | 显示 assistant 最终文本 |
| `render_assistant_delta(text)` | 流式文本 delta（实际实现新增） |
| `render_tool_result_summary(result)` | 工具执行结果摘要：tool_name + call_id + ok/error |
| `render_error(message)` | 统一错误输出格式 |
| `render_clear(old_id, new_id)` | /clear 后的提示 |
| `render_resume(session_id, path, workspace)` | /resume 成功提示 |

**长内容截断**：`_preview()` 函数将长内容截断为 180 字符（`PREVIEW_CHARS = 180`），避免把完整工具结果刷满终端。

### 3.3 `ui/cli/commands.py` —— Slash Command 处理

命令派发以 `/` 前缀识别。每个命令返回 `CommandResult`，不抛异常控制流。

**命令解析细节**

- `_split_command(line)`：先按第一个空格分离命令名和参数。
- 对 `/resume` 特殊处理：参数不经过 `split()`（因为 Windows 路径的反斜杠会被 POSIX shlex 误解析为转义符）。
- `_strip_matching_quotes()`：去掉参数外层的成对引号。

**七个内置命令**

| 命令 | 实现函数 | 关键行为 |
|---|---|---|
| `/help` | 直接调用 `renderer.render_help()` | 无副作用 |
| `/tools` | 遍历 `runtime.registry.descriptors()` | 不从 CLI 另写工具列表 |
| `/status` | 聚合 `runtime.state`、`message_store`、`trace_recorder` | 显示 usage、transcript 路径 |
| `/history [n]` | `_recent_messages()` 取最近 n 条 | 默认 20 条 |
| `/trace [n]` | `_recent_trace_records()` | 实际新增，见下文 |
| `/clear` | `_clear()` → `state.start_new_session()` + `message_store.clear_for_new_session()` | 旧 session 保留在 `.onecode/` |
| `/resume <target>` | `_resume()` → `resolve_resume_target()` → `MessageStore.from_transcript()` | 重建 runtime |
| `/exit` / `/quit` | `message_store.flush_transcript()` | 确保 JSONL 落盘 |

**`/resume` 的路径解析逻辑**（`resolve_resume_target`）

1. 将参数转为 `Path`，若非绝对路径则基于 `workspace` 解析。
2. 若以 `.jsonl` 结尾或已是文件 → 直接作为 messages 文件路径。
3. 否则作为 session id → 解析为 `workspace / ".onecode" / <id> / "messages.jsonl"`。
4. 若文件不存在 → 抛出 `ValueError`，`_resume()` 捕获后打印错误，保留当前 runtime 不变。

### 3.4 `ui/cli/app.py` —— 装配与主循环

这是 CLI 的入口和编排中心，不包含具体业务逻辑。

**`build_runtime(workspace)` —— 依赖装配**

```python
def build_runtime(workspace: Path) -> CliRuntime:
    state = RuntimeState()
    message_store = MessageStore(transcript_root=..., session_id=..., cwd=...)
    registry = ToolRegistry([read_file_descriptor(), edit_file_descriptor(), ...])
    context_engine = ContextEngine(message_store, prompt_assembler=..., tool_schema_provider=registry)
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    tool_executor = RegistryToolExecutor(registry, guard=guard, hooks=...)
    model_client = create_model_client(".env")
    loop = AgentLoop(state=state, message_store=message_store, context_engine=..., model_client=..., tool_executor=...)
    return CliRuntime(...)
```

这完全遵循了计划的第二阶段要求：`build_runtime` 读取 `.env` 创建模型客户端、装配所有依赖、创建 `AgentLoop`。

**`main_loop_async(runtime)` —— 异步主循环**

```
while True:
    line = await input("onecode> ")
    if line 以 "/" 开头 → handle_command(runtime, line)
    否则 → async for event in runtime.loop.stream(line):
               event.type == "assistant_delta" → 实时打印 token
               event.type == "tool_result"     → 打印工具结果摘要
               event.type == "completed"       → 获取最终文本
```

**`main()` —— 入口函数**

```python
def main(argv=None) -> int:
    workspace = Path.cwd()
    runtime = build_runtime(workspace)
    return asyncio.run(main_loop_async(runtime))
```

支持 `python -m ui.cli.app` 直接启动。

---

## 4. 计划与实际实现的关键差异

### 4.1 流式输出（超出计划）

计划明确指出：*"当前主循环没有 streaming 或可观测事件订阅接口。CLI 第一版不能承诺实时 token 或工具进度渲染。"*

但实际实现中，当 `AgentLoop` 新增了 `stream()` 异步生成器后，CLI 立即接入了：

```python
async for event in runtime.loop.stream(line):
    if event.type == "assistant_delta":
        print(renderer.render_assistant_delta(event.text), end="", flush=True)
    elif event.type == "tool_result":
        print(renderer.render_tool_result_summary(event.result))
```

这使用户可以实时看到模型输出的 token 流和工具调用进度，远超计划预期。

### 4.2 工具数量（超出计划）

计划要求固定注册两个工具：`read_file` 和 `edit_file`。

实际实现注册了五个工具：

```python
registry = ToolRegistry([
    read_file_descriptor(),
    edit_file_descriptor(),
    glob_descriptor(),
    grep_descriptor(),
    bash_descriptor(),
])
```

这是因为 `glob`、`grep`、`bash` 工具在后续计划中陆续完成后自然加入。

### 4.3 权限交互（超出计划）

计划明确：*"第一版不做权限交互。"*

实际实现引入了 `CliPermissionPrompter` 和 `PermissionPolicy`，在工具需要权限确认时弹出 CLI 交互式提示。这是后续权限计划自然演进的结果。

### 4.4 Observability Trace（超出计划）

计划没有包含 trace 功能。实际实现添加了：

- `/trace [n]` 命令：查看最近的 trace 事件
- `JsonlTraceSink`：将 trace 写入 `.onecode/<session_id>/trace.jsonl`
- `TraceRecorder`：在 `build_runtime` 中装配并注入整个管线

---

## 5. 架构原则验证

### 5.1 主循环保持薄

`ui/cli/app.py` 中的主循环只做三件事：
1. 读取用户输入
2. 如果是 slash command → 派发到 `commands.py`
3. 否则 → 调用 `runtime.loop.stream(line)` 并渲染事件

它不 import 具体工具、不 import guard 实现、不 import provider 细节。

### 5.2 工具列表来自 Registry

`/tools` 命令通过 `runtime.registry.descriptors()` 获取工具列表，不从 CLI 另写一份。

### 5.3 模型可见工具 Schema 来自同一 Registry

`ContextEngine` 的 `tool_schema_provider` 指向同一个 `ToolRegistry`，确保模型可见的工具与 CLI 显示的工具完全一致。

### 5.4 Guard 在 Executor 层执行

CLI 不直接调用任何工具 handler。所有工具调用走 `RegistryToolExecutor`，在 executor 内部由 `SandboxGuard` 检查路径安全。

---

## 6. 测试覆盖

计划要求新增测试文件 `tests/test_cli_commands.py` 和 `tests/test_cli_resume.py`。实际创建：

```
tests/test_cli_commands.py    # 覆盖 /help, /tools, /status, /history, /clear, 未知命令
tests/test_cli_resume.py      # 覆盖 /resume 的路径解析和 JSONL 恢复
tests/test_cli_permissions.py # 权限交互测试（后续新增）
```

测试使用 fake runtime 对象，不触发真实 provider。

---

## 7. 验收标准对照

| # | 验收标准 | 状态 |
|---|---|---|
| 1 | `uv run python -m ui.cli.app` 启动 CLI，无需额外依赖 | ✓ |
| 2 | `/tools` 显示已注册工具，与模型可见 schema 同源 | ✓ |
| 3 | 普通 prompt 调用 `AgentLoop.run()`，工具走 executor+guard | ✓ |
| 4 | `/history` 显示消息摘要，区分 role，截断长内容 | ✓ |
| 5 | `/clear` 生成新 session id，旧 `.onecode/` 保留 | ✓ |
| 6 | `/resume <session_id>` 恢复 JSONL，追加对话 | ✓ |
| 7 | `/exit` 或 EOF 前 `flush_transcript()` 落盘 | ✓ |
| 8 | `uv run python -m compileall ...` 和 `uv run python -m pytest tests -q` 均通过 | ✓ |

---

## 8. 总结

CLI Main UI Plan 的成功实施体现在：

1. **忠实于计划的轻量设计**：无 Rich/Textual/prompt-toolkit，纯标准库实现，保持了一版 CLI 的简约性。
2. **清晰的模块边界**：类型（types.py）、渲染（renderer.py）、命令（commands.py）、装配与循环（app.py）各司其职，无循环依赖。
3. **可扩展的架构**：当后续 streaming、permissions、observability trace 等能力成熟后，CLI 只需按计划接口接入，无需重写核心结构。
4. **完整的验收**：所有 8 条验收标准均满足，compile 和全量 pytest 通过。

该计划为所有后续 CLI 能力（结构化 observability、权限交互、provider/recovery 友好错误路径）留下了自然的接入点，是第一版工作 CL 界面的稳固基石。
