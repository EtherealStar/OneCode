# CLI Code Agent MVP 实现计划
 
目标：实现一个类似 Claude Code 的、运行于 CLI 的最小可用 Code Agent。MVP 只做单 Agent、串行工具执行、动态系统提示词、基础钩子、基础上下文压缩和基础错误恢复。

## 参考资料

本计划基于 `docs/references/` 中的这些实现线索：

- `s01_agent_loop`：核心 `while True` 循环。模型调用工具则执行工具并继续；没有工具调用则输出最终结果并结束。
- `s02_tool_use`：工具定义和工具分发分离，工具调用按模型返回顺序执行。
- `s03_permission`：权限检查应作为工具执行前的固定关口。
- `s04_hooks`：把扩展点挂在循环外，主循环只触发 hook，不把扩展逻辑写死进循环。
- `s08_context_compact`：压缩顺序采用 `tool_result_budget -> snip/sliding window -> micro compact -> full compact`，上下文超限时使用 reactive compact。
- `s10_system_prompt`：系统提示词运行时组装，按真实运行状态决定加载哪些片段。
- `s11_error_recovery`：429、输出被截断、上下文超限分别走不同恢复路径。
- `主循环和重建上下文/query.ts`：流式模式下不要只依赖 `stop_reason == "tool_use"`，应在流式内容中发现工具调用后设置后续循环信号。
- `主循环和重建上下文/QueryEngine.ts`：流式结束后从 usage 累积 token 使用量，并把 usage 作为会话状态的一部分。

## MVP 边界

必须实现：

- Python CLI，可交互输入，也可接受单次 prompt。
- Agent 主循环：模型返回工具调用则执行工具并继续；没有工具调用则输出最终文本并终止当前任务。
- 工具注册系统：工具有元数据、JSON schema、执行函数、并发/读写能力标记；运行时动态组装传给模型的工具列表。
- MVP 工具串行执行，保留未来按元数据分块并发的接口。
- 钩子系统：至少支持工具相关钩子和压缩相关钩子。
- 压缩系统：自动清理旧工具结果、滑动窗口、达到 80% 上下文窗口后自动全量压缩。
- usage 跟踪：模型流式输出结束后读取 `response.usage`，更新当前 token 使用量和上下文窗口占用比例。
- 基础错误处理：429、输出中断或 `max_tokens`、上下文超限分别处理。
- 动态系统提示词：提示词片段写在 Python 代码中，按运行时状态组装。

暂不实现：

- 记忆系统。只预留 `PostCompact` 扩展点，未来在压缩后接入。
- 子 Agent、任务 DAG、MCP、插件、长期会话恢复、复杂权限配置。
- 工具并发执行。MVP 只实现串行，保留 `partition_tool_calls()` 的空壳或测试接口。

待确认但不阻塞 MVP：

- 模型接口。MVP 使用 Chat Completions 兼容 HTTP 接口，不绑定任何模型提供商 SDK。
- 包名。当前实现使用 `onecode`。
- 权限策略默认值。MVP 建议默认允许读操作、写操作限制在工作区、危险 shell 命令需要确认或直接拒绝。

## 目标目录结构

```text
onecode/
  __init__.py
  cli.py
  config.py
  loop.py
  model_client.py
  prompts.py
  state.py
  errors.py
  hooks.py
  compaction.py
  transcript.py
  tools/
    __init__.py
    base.py
    registry.py
    builtin.py
tests/
  test_loop.py
  test_tools.py
  test_hooks.py
  test_compaction.py
  test_errors.py
```

如果仓库后续已有应用结构，应把这些模块并入现有包，而不是强行创建新顶层包。

## 核心数据结构

`state.py`：

```python
@dataclass
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    context_window: int = 200_000
    occupied_ratio: float = 0.0

@dataclass
class AgentState:
    messages: list[dict]
    usage: UsageSnapshot
    turn_count: int = 0
    has_reactive_compacted: bool = False
    max_output_recovery_count: int = 0
    last_transition: str | None = None
```

`tools/base.py`：

```python
@dataclass(frozen=True)
class ToolMeta:
    name: str
    description: str
    input_schema: dict
    read_only: bool
    concurrency_safe: bool
    mutates_filesystem: bool = False
    requires_permission: bool = False
    max_result_chars: int | None = 50_000
    timeout_seconds: int = 120

@dataclass(frozen=True)
class Tool:
    meta: ToolMeta
    handler: Callable[..., str]
```

元数据设计要服务两个阶段：

- MVP：`ToolRegistry.api_schemas()` 运行时组装模型可见工具，`execute_serial()` 查找 handler 并顺序执行。
- 未来：`partition_tool_calls()` 根据 `read_only`、`concurrency_safe` 和工具输入，把连续可并发工具分成 batch，不可并发工具单独串行执行。

## 主循环设计

主循环只做编排，不写业务扩展逻辑：

```python
while True:
    state.messages = compaction.prepare_before_model_call(state)
    system_prompt = get_system_prompt(runtime_context)
    tools = tool_registry.api_schemas(runtime_context)

    response = model_client.stream_chat(
        system=system_prompt,
        messages=state.messages,
        tools=tools,
        max_output_tokens=current_max_output_tokens,
    )

    state.usage = usage_from_response(response.usage, model_profile)
    state.messages.append(response.assistant_message)

    if response.stop_reason == "max_tokens" or response.output_interrupted:
        handle_output_interruption()
        continue

    if not response.tool_calls:
        stop_result = hooks.emit("Stop", state=state)
        if stop_result.force_continue:
            state.messages.append(stop_result.to_user_message())
            continue
        return response.final_text

    tool_results = execute_tool_calls_serial(response.tool_calls)
    state.messages.append({"role": "user", "content": tool_results})
```

续轮信号以 `response.tool_calls` 是否为空为准。`stop_reason` 可以作为辅助信息，但不要作为唯一依据，因为流式响应中 `stop_reason` 可能晚于工具块出现。

## 模型客户端

`model_client.py` 负责把具体 SDK 输出归一化：

```python
@dataclass
class LLMResponse:
    assistant_message: dict
    final_text: str
    tool_calls: list[ToolCall]
    stop_reason: str | None
    usage: dict | None
    output_interrupted: bool = False
```

实现要点：

- CLI 实时打印文本 delta。
- 流式期间收集 tool call block，流式结束后统一返回 `LLMResponse`。
- 流式结束后读取 `response.usage` 或 SDK 等价字段，更新 `UsageSnapshot`。
- `UsageSnapshot.occupied_ratio = input_tokens / (context_window - reserved_output_tokens)`。没有 usage 时用本地估算器兜底，但一旦有真实 usage，以真实 usage 为准。
- 模型配置中维护 `context_window`、默认 `max_output_tokens`、升级后的 `max_output_tokens`。

## 工具系统

MVP 内置工具：

- `bash`：执行 shell 命令，超时 120 秒，输出截断或持久化。
- `read_file`：读取工作区内文件，可传 `limit`。
- `write_file`：写入工作区内文件。
- `edit_file`：一次精确替换。
- `glob`：按 pattern 搜索工作区文件。

工具执行流程：

1. 从 `ToolRegistry` 查找工具。
2. 校验参数结构。MVP 可用 `jsonschema`，也可先做轻量必填字段检查。
3. 触发 `PreToolUse` hooks。
4. 如果 hook 阻断，返回 `is_error=True` 的 tool result。
5. 执行 handler，捕获异常并转成 tool result。
6. 按 `max_result_chars` 截断或交给压缩系统持久化。
7. 触发 `PostToolUse` hooks。

MVP 串行执行：

```python
def execute_tool_calls_serial(tool_calls):
    results = []
    for call in tool_calls:
        results.append(execute_one_tool(call))
    return results
```

未来并发接口先保留：

```python
def partition_tool_calls(tool_calls, registry):
    # MVP 返回 [[call1], [call2], ...]
    # 未来把连续 concurrency_safe 的调用合并成同一个 batch。
    return [[call] for call in tool_calls]
```

## 钩子系统

`hooks.py`：

```python
HookEvent = Literal[
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "ToolError",
    "PreCompact",
    "PostCompact",
    "Stop",
]
```

MVP hook 返回值：

```python
@dataclass
class HookResult:
    blocked: bool = False
    message: str | None = None
    updated_input: dict | None = None
    force_continue: bool = False
```

内置 hooks：

- `workspace_permission_hook`：写文件必须限制在工作区内。
- `dangerous_bash_hook`：拒绝明显危险命令，例如 `rm -rf /`、`sudo`、`shutdown`。
- `tool_log_hook`：CLI 显示工具名和参数摘要。
- `compact_log_hook`：压缩前后输出摘要，未来记忆系统接入 `PostCompact`。

## 压缩系统

压缩在每次模型调用前执行，顺序固定：

1. `tool_result_budget`：先处理最近一轮超大的工具结果。超过预算的结果写入 `.onecode/tool-results/`，上下文中保留路径和预览。
2. `cleanup_old_tool_results`：只保留最近 N 个工具结果全文，旧结果替换为占位符。
3. `sliding_window`：保留 compact summary、最初任务约束和最近消息尾部，裁剪中间旧消息。
4. `auto_full_compact`：如果真实 usage 显示上下文占用达到 80%，调用总结模型做全量压缩。

配置建议：

```python
KEEP_RECENT_TOOL_RESULTS = 3
TOOL_RESULT_TOTAL_BUDGET_CHARS = 200_000
TOOL_RESULT_PREVIEW_CHARS = 2_000
SLIDING_WINDOW_MAX_MESSAGES = 60
AUTO_COMPACT_RATIO = 0.80
POST_COMPACT_TARGET_RATIO = 0.55
MAX_CONSECUTIVE_COMPACT_FAILURES = 3
```

全量压缩流程：

1. 触发 `PreCompact`。
2. 写完整 transcript 到 `.onecode/transcripts/`。
3. 用无工具模型调用总结当前历史，要求保留目标、已完成工作、关键发现、修改文件、用户约束、下一步。
4. 用一条 compact summary message 加最近尾部消息替换旧历史。
5. 重置 `has_reactive_compacted`。
6. 触发 `PostCompact`，为未来记忆系统预留入口。

上下文超限时的 reactive compact：

- 捕获 `prompt_too_long`、HTTP 413、`context_length_exceeded` 等错误。
- 如果本轮还没 reactive compact，则立即写 transcript，执行更激进的全量压缩，只保留 summary 和最近 3 到 5 条消息，然后重试同一次模型调用。
- 如果 reactive compact 后仍超限，停止当前任务并输出可操作错误，不进入 Stop hook，避免错误消息和 hook 互相追加导致死循环。

## 错误处理

429 rate limit：

- 使用 `Retry-After` 优先。
- 没有 `Retry-After` 时指数退避加 jitter：`min(0.5 * 2**attempt, 32)` 秒，加 0 到 25% 抖动。
- 重试同一次请求，不追加部分 assistant message。
- 超过最大重试次数后退出当前任务，返回清晰错误。

输出中断：

- `stop_reason == "max_tokens"`：第一次把 `max_output_tokens` 从默认值升级到更大值，重试同一请求，不追加截断输出。
- 升级后仍被截断：追加已产生的 assistant 内容，再追加 continuation user message，要求模型从中断处继续，最多 3 次。
- 流式连接中断但没有完整响应：丢弃不完整 tool call，重试同一请求一次；仍失败则输出中断错误。

上下文超限：

- 不走 429 backoff。
- 立即 reactive compact，然后重试。
- reactive compact 已尝试仍失败，停止并提示用户手动缩小任务或清理上下文。

工具错误：

- handler 抛异常时捕获，返回 `tool_result` 且 `is_error=True`，让模型有机会自我修正。
- 未知工具返回错误 tool result，不让主循环崩溃。
- 参数校验失败返回错误 tool result。

## 动态系统提示词

`prompts.py` 中写 Python 函数，不使用独立 prompt 文件：

```python
def assemble_system_prompt(ctx: PromptContext) -> str:
    sections = [
        identity_section(),
        behavior_section(),
        workspace_section(ctx.cwd),
        tool_policy_section(ctx.enabled_tools),
        compaction_section(ctx.compaction_enabled),
    ]
    if ctx.permission_mode:
        sections.append(permission_section(ctx.permission_mode))
    return "\n\n".join(section for section in sections if section)
```

组装原则：

- 固定身份和行为规则始终加载。
- 工具策略根据当前注册工具动态生成。
- 工作区、系统平台、当前日期等运行时状态动态注入。
- 记忆 section 暂不加载，只保留接口。
- 使用稳定 section 顺序，便于未来接入 API prompt cache。

## CLI 体验

命令形态：

```bash
onecode "fix the failing tests"
onecode
```

交互命令：

- `/exit`：退出。
- `/clear`：清空当前会话消息。
- `/compact`：手动触发全量压缩。
- `/tools`：列出当前启用工具和元数据摘要。

输出要求：

- 流式显示模型文本。
- 工具调用显示工具名和简短参数摘要。
- 工具结果默认只显示预览，完整内容进入消息上下文或持久化文件。
- 错误恢复要明确显示当前动作，例如 rate limit backoff、reactive compact、max output retry。

## 实施步骤

1. 创建项目骨架和配置读取。
   - 支持 `ONECODE_MODEL`、`ONECODE_API_KEY`、`ONECODE_BASE_URL`、`ONECODE_CONTEXT_WINDOW`。
   - 定义 `AgentConfig`、`ModelProfile`、`AgentState`。

2. 实现模型客户端抽象。
   - 完成流式输出归一化。
   - 收集文本、tool calls、stop reason、usage。
   - 提供 fake client 供单元测试使用。

3. 实现工具注册系统和内置工具。
   - 完成 `ToolMeta`、`Tool`、`ToolRegistry`。
   - 完成 `bash/read_file/write_file/edit_file/glob`。
   - 所有文件工具必须走工作区路径校验。

4. 实现 hook registry。
   - 支持注册、按事件顺序执行、阻断和修改输入。
   - 把权限检查和日志作为内置 hook 接入。

5. 实现主循环。
   - `while True` 编排模型调用、工具执行和终止。
   - 以 `response.tool_calls` 是否为空决定是否继续。
   - 工具执行结果以 user/tool result 消息追加回历史。

6. 实现压缩系统。
   - 先做工具结果预算、旧工具结果清理和滑动窗口。
   - 再接入 80% usage 阈值触发全量压缩。
   - 实现 `/compact` 手动压缩。

7. 实现错误恢复。
   - 429 backoff。
   - `max_tokens` 升级和 continuation。
   - prompt too long reactive compact。
   - 工具异常和未知工具转为 tool result。

8. 补测试和最小文档。
   - fake model 驱动主循环测试。
   - compaction 顺序测试。
   - hook 阻断测试。
   - usage 80% 触发测试。
   - 429、max_tokens、prompt_too_long 恢复路径测试。

## 验收标准

- 输入一个无需工具的问题时，CLI 流式输出答案并结束当前任务。
- 输入一个需要读文件的问题时，模型调用 `read_file`，Agent 执行后继续，最终无工具调用时结束。
- 同一轮出现多个工具调用时，MVP 按返回顺序串行执行。
- 工具元数据能动态组装成模型工具 schema，禁用工具后不会出现在 schema 中。
- `PreToolUse` 能阻断危险 bash 或越界文件写入，并把阻断结果返回给模型。
- 旧工具结果会被占位符替换，大工具结果会被写入 `.onecode/tool-results/`。
- `response.usage` 显示上下文达到 80% 后，会自动写 transcript 并全量压缩。
- 429 会 backoff 后重试，不污染消息历史。
- `max_tokens` 会先升级输出 token，再使用 continuation prompt。
- 上下文超限会 reactive compact 后重试，重试失败时明确退出。
- 记忆系统没有实现，但 `PostCompact` hook 已能作为未来接入点。

## 后续版本方向

- 工具分块并发：按连续 `concurrency_safe=True` 的工具调用组成 batch，并设置并发上限。
- 更细权限系统：允许/拒绝/询问规则、会话级授权、命令语义分类。
- 记忆系统：在 `PostCompact` 后抽取长期偏好、项目事实和待办状态。
- 子 Agent：独立上下文、权限向父 Agent 冒泡、最终只回传摘要。
- 会话持久化：保存消息、transcript、工具结果索引和 usage。
- Prompt cache：把系统提示词拆成稳定静态 section 和动态 section。
