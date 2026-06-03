# OneCode 主循环与上下文重建实现计划

目标：只实现项目级主循环和它每轮模型调用必须依赖的上下文重建边界。当前阶段不实现完整工具系统、guard、CLI、真实 provider、压缩策略、hook 系统或可观测性系统；只为它们预留主循环所需的最小接口，保证后续模块可以接入而不需要重写主循环。

本计划的核心交付是：

- `core/loop.py`：薄主循环。
- `core/context_engine.py`：每轮重建 `ContextSnapshot`。
- `core/runtime_state.py`：主循环状态。
- `core/transitions.py`：主循环 transition reason。
- 最小协议类型：模型客户端、工具执行器、消息存储、prompt assembler、tool schema provider。
- 单元测试：用 fake model、fake tool executor、fake context dependencies 验证主循环行为。

## 参考依据

- `architecture.md`
  - `core/loop.py` 只负责 agent 生命周期编排。
  - `core/context_engine.py` 负责把当前运行状态转换为一次模型调用所需的完整快照。
  - 主循环不能 import 具体工具目录，不能 import 具体 provider，不能实现路径规则、prompt 文本、压缩策略或 UI 渲染。

- `docs/design-docs/core-beliefs.md`
  - 主循环只表达“准备上下文 -> 调模型 -> 执行工具或停止”的编排。
  - 新能力应进入 registry、hook、prompt section、compaction layer、state transition 或 model client。
  - 错误恢复应成为明确 transition。
  - 上下文是受管理的工作内存，但本阶段只实现重建边界，不实现完整压缩系统。

- `docs/references/s01_agent_loop/`
  - 最小循环是 `while True`：模型需要工具则执行并回填工具结果；没有工具调用则结束。
  - 教学版本用 `stop_reason == "tool_use"`，但项目级主循环应以实际 `tool_calls` 作为续轮信号。

- `docs/references/主循环和重建上下文/query.ts`
  - 每轮循环从状态重建 `messagesForQuery`。
  - `stop_reason` 在流式场景可能晚到或为空，不能作为唯一续轮信号。
  - 状态需要保存 `turnCount`、`transition`、`hasAttemptedReactiveCompact`、`maxOutputTokensRecoveryCount` 等字段；本阶段只实现字段和 transition，不实现完整恢复策略。

- `docs/references/主循环和重建上下文/QueryEngine.ts`
  - 用户消息进入循环前应先进入会话状态，后续由上下文重建生成模型可见消息。
  - usage 是 session state 的一部分。
  - compact boundary 之后的裁剪属于上下文服务责任；本阶段只预留接口。

## 当前状态

当前仓库已经不再保留 MVP demo 源码。`docs/exec-plans/completed/cli-code-agent-mvp.md` 只能作为历史参考，不能作为当前实现基础。

当前阶段不追求一次性实现完整 runtime。它只建立主循环和上下文重建的稳定骨架，使后续工具、guard、prompt、provider、compaction、hook、CLI 和 observability 能按架构逐步接入。

## 本阶段范围

### 必须实现

- `core/loop.py`
  - 接收用户输入。
  - 追加用户消息。
  - 每轮调用 `ContextEngine.build_for_model()`。
  - 调用注入的 `ModelClient`。
  - 追加 assistant message。
  - 如果 `LLMResponse.tool_calls` 非空，调用注入的 `ToolExecutor`，追加 tool result message，并继续。
  - 如果没有 tool calls，返回最终文本。
  - 处理 `max_turns`。
  - 设置基础 transition。

- `core/context_engine.py`
  - 从 `MessageStore` 读取当前消息。
  - 调用可选的 `ContextPreparer` 接口，允许后续 compaction 接入。
  - 调用 `PromptAssembler` 接口生成 system prompt。
  - 调用 `ToolSchemaProvider` 接口生成 tool schemas。
  - 返回不可变 `ContextSnapshot`。

- `core/runtime_state.py`
  - 保存 turn count、usage、transition、恢复字段和 session metadata。
  - 不保存复杂业务状态。

- `core/transitions.py`
  - 定义本阶段主循环会设置的 transition reason。

- 最小协议或类型
  - `LLMResponse`
  - `ToolCall`
  - `ModelUsage` 或 `UsageSnapshot`
  - `ContextSnapshot`
  - `MessageStore`
  - `ModelClient`
  - `ToolExecutor`
  - `PromptAssembler`
  - `ToolSchemaProvider`
  - `ContextPreparer`

- 测试
  - 主循环 tool call 续轮。
  - 主循环最终停止。
  - 主循环不依赖 `stop_reason == "tool_use"`。
  - 每轮都会重建 `ContextSnapshot`。
  - tool results 会进入下一轮上下文来源。
  - `max_turns` transition。

### 只预留接口，不实现功能

- 具体工具实现。
- 工具 registry 的完整发现、metadata、schema 转换。
- 文件 guard、路径安全、permission ask/deny。
- hook registry 和 builtin hooks。
- compaction 策略、transcript、result store。
- prompt section 文案和动态 prompt 完整策略。
- 真实 Chat Completions provider。
- CLI。
- observability JSONL trace。
- 流式输出。
- 并发工具执行。
- session resume、memory、task、plugin、skill。

这些能力后续实现时必须接入本阶段定义的接口，而不是修改主循环核心结构。

## 设计

### 主循环边界

`core/loop.py` 的主流程只做编排：

```text
accept user prompt
append user message
while running:
  build ContextSnapshot
  call model
  append assistant message
  if response.tool_calls:
    execute tool calls through ToolExecutor
    append tool result message
    continue
  return final text
```

主循环不做：

- 具体工具选择。
- 具体路径判断。
- prompt 文案拼接。
- provider response 字段解析。
- 压缩算法。
- hook 分发。
- CLI 输出。

### ContextSnapshot

`ContextSnapshot` 是主循环调用模型的唯一上下文输入。

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class ContextSnapshot:
    system_prompt: str
    messages: tuple[dict, ...]
    tool_schemas: tuple[dict, ...] = field(default_factory=tuple)
    usage_hints: dict = field(default_factory=dict)
    transcript_refs: tuple[str, ...] = field(default_factory=tuple)
    transition: str | None = None
```

本阶段 `transcript_refs` 可以始终为空；字段保留给后续 transcript/compaction。

### ContextEngine

`ContextEngine.build_for_model(state)` 的顺序固定：

1. 从 `MessageStore` 读取当前内部消息。
2. 交给 `ContextPreparer.prepare(messages, state)`。
3. 交给 projector 或最小投影函数生成模型可见 messages。
4. 调用 `PromptAssembler.assemble(state)`。
5. 调用 `ToolSchemaProvider.tool_schemas(state)`。
6. 返回 `ContextSnapshot`。

第一阶段的 `ContextPreparer` 可以是 no-op。它存在的原因是避免未来把 compaction 塞回主循环。

### RuntimeState

```python
from dataclasses import dataclass, field

@dataclass
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

@dataclass
class RuntimeState:
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    turn_count: int = 0
    max_turns: int = 20
    has_attempted_reactive_compact: bool = False
    max_output_recovery_count: int = 0
    last_transition: str | None = None
    session_id: str | None = None
```

`has_attempted_reactive_compact` 和 `max_output_recovery_count` 本阶段只作为状态字段保留，不实现对应恢复流程。

### ModelClient 协议

```python
class ModelClient(Protocol):
    def send(self, snapshot: ContextSnapshot) -> LLMResponse:
        ...
```

真实 provider 不在本阶段实现。测试使用 fake model client。

### LLMResponse

```python
@dataclass
class LLMResponse:
    assistant_message: dict
    final_text: str
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str | None = None
    usage: UsageSnapshot | None = None
    output_interrupted: bool = False
```

主循环续轮只看 `tool_calls`，不看 `stop_reason == "tool_use"`。

### ToolExecutor 协议

```python
class ToolExecutor(Protocol):
    def execute(self, tool_calls: tuple[ToolCall, ...], state: RuntimeState) -> list[dict]:
        ...
```

本阶段不实现具体工具。测试使用 fake tool executor 返回 tool result blocks。

### MessageStore

第一阶段可以实现内存版 message store：

```python
class MessageStore:
    def append_user(self, content: str | list[dict]) -> dict:
        ...

    def append_assistant(self, message: dict) -> dict:
        ...

    def append_tool_results(self, result_blocks: list[dict]) -> dict:
        ...

    def current_messages(self) -> tuple[dict, ...]:
        ...
```

后续 transcript、compact boundary、message projection 都应扩展 message/context 服务，不应改主循环。

### Transition

本阶段只实现主循环需要的 transition：

```python
class TransitionReason(StrEnum):
    TOOL_USE = "tool_use"
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
```

可以预留但不要求实现：

- `rate_limit_retry`
- `reactive_compact_retry`
- `max_output_tokens_escalate`
- `max_output_tokens_recovery`
- `stop_hook_continue`

## 主循环伪代码

```python
def run(prompt: str) -> str:
    message_store.append_user(prompt)
    return run_loop()

def run_loop() -> str:
    while True:
        state.turn_count += 1
        if state.turn_count > state.max_turns:
            state.last_transition = "max_turns"
            return "Stopped: maximum turn count reached."

        snapshot = context_engine.build_for_model(state)
        response = model_client.send(snapshot)

        if response.usage:
            state.usage.add(response.usage)

        message_store.append_assistant(response.assistant_message)

        if response.tool_calls:
            result_blocks = tool_executor.execute(response.tool_calls, state)
            message_store.append_tool_results(result_blocks)
            state.last_transition = "tool_use"
            continue

        state.last_transition = "completed"
        return response.final_text
```

## 实施步骤

1. 建立最小目录
   - 创建 `core/`、`services/model/`、`services/tools/`、`services/context/`、`tests/`。
   - 只创建本阶段需要的文件。
   - 不创建 `prompts/`、具体 `tools/*`、`services/guard/*`、`ui/cli/*`、`infrastructure/providers/*`。
   - prompt 组装在本阶段只作为 `ContextEngine` 注入协议或测试 fake，不落地完整 prompt 模块。

2. 定义类型
   - `core/runtime_state.py`
   - `core/transitions.py`
   - `services/model/types.py`
   - `services/tools/types.py`
   - `services/context/snapshot.py`

3. 实现 message store
   - `services/context/message_store.py`
   - 内存实现即可。
   - 支持 append user、assistant、tool result。

4. 实现上下文重建
   - `core/context_engine.py`
   - 接收 message store、context preparer、prompt assembler、tool schema provider。
   - no-op preparer 和静态 prompt/schema provider 可作为测试或默认实现。

5. 实现主循环
   - `core/loop.py`
   - 只依赖协议和 context engine。
   - 不导入具体工具、provider、guard、CLI。

6. 建立测试
   - `tests/test_loop.py`
   - `tests/test_context_engine.py`
   - 用 fake dependencies 驱动行为。

## 测试计划

- `test_loop_stops_without_tool_calls`
  - fake model 返回无 tool calls。
  - loop 返回 final text。
  - transition 为 `completed`。

- `test_loop_continues_when_tool_calls_present`
  - fake model 第一轮返回 tool call，第二轮返回 final text。
  - fake tool executor 被调用一次。
  - tool result message 写入 message store。
  - context engine 被调用两次。

- `test_loop_uses_tool_calls_not_stop_reason`
  - fake model 返回 `tool_calls` 且 `stop_reason=None`。
  - loop 仍继续。
  - fake model 返回无 `tool_calls` 且 `stop_reason="tool_use"`。
  - loop 仍停止。

- `test_loop_max_turns`
  - fake model 持续返回 tool calls。
  - 超过 max turns 后停止。
  - transition 为 `max_turns`。

- `test_context_engine_rebuilds_snapshot_from_current_messages`
  - message store 追加用户消息和 tool result。
  - snapshot messages 反映当前消息。
  - prompt assembler 和 tool schema provider 都被调用。

- `test_context_preparer_can_replace_projected_messages`
  - fake preparer 返回裁剪后的 messages。
  - snapshot 使用 preparer 输出。
  - 证明后续 compaction 可以接入此点。

## 验收标准

- `core/loop.py` 只做主循环编排。
- `core/loop.py` 不 import 具体工具、具体 provider、guard、CLI 或 compaction 实现。
- 每轮模型调用前都会通过 `ContextEngine` 重建 `ContextSnapshot`。
- 主循环只以 `LLMResponse.tool_calls` 是否为空判断是否继续。
- tool result 通过 `ToolExecutor` 接口返回，并写回 message store。
- 无 tool calls 时返回最终文本并设置 `completed`。
- 超过 max turns 时停止并设置 `max_turns`。
- 测试使用 fake dependencies 验证主循环，不需要真实 API key、不需要真实工具、不需要文件系统 guard。

## 明确不做

本阶段不实现以下内容：

- 具体工具。
- 工具 registry 的完整实现。
- guard 和路径安全。
- hook 系统。
- compaction、transcript、result store。
- 真实 provider。
- CLI。
- observability trace。
- 流式输出。
- 错误恢复的完整执行逻辑。

这些内容在后续独立计划中实现，只能通过本阶段留下的接口接入。
