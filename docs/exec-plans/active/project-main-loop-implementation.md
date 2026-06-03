# OneCode 项目主循环实现计划

目标：把当前 MVP 的 `AgentLoop` 演进为项目级主循环。主循环仍保持薄，只负责稳定编排；上下文重建、工具执行、压缩、错误恢复、hook 和观测事件分别进入各自边界。

## 参考依据

- `docs/design-docs/core-beliefs.md`
  - 主循环只表达“准备上下文 -> 调模型 -> 执行工具或停止”的编排。
  - 新能力优先进入 registry、hook、prompt section、compaction layer、state transition 或 model client。
  - 上下文是受管理的工作内存；全量压缩前必须写 transcript。
  - 错误恢复应成为明确 transition，而不是散落异常分支。

- `docs/references/s01_agent_loop/`
  - 最小循环是 `while True`：模型需要工具则执行并回填工具结果；没有工具调用则结束。
  - mvp使用 `stop_reason == "tool_use"`，但完整实现不能只依赖这个字段。

- `docs/references/主循环和重建上下文/query.ts`
  - 每轮循环从状态重建 `messagesForQuery`，而不是无条件把完整历史发给模型。
  - 续轮信号以内容里实际出现的 tool use 为准；流式场景中 `stop_reason` 可能晚到或为空。
  - 状态需要保存 `turnCount`、`transition`、`hasAttemptedReactiveCompact`、`maxOutputTokensRecoveryCount` 等恢复字段。
  - 模型调用前按顺序处理工具结果预算、snip/sliding window、micro compact、auto compact。

- `docs/references/主循环和重建上下文/QueryEngine.ts`
  - Engine 持有会话级 mutable messages、usage、permission denials、read-file cache 等跨 turn 状态。
  - 用户消息进入循环前应先记录 transcript，保证请求中断时仍可恢复。
  - compact boundary 之后应裁剪旧 mutable messages，让后续 query 只从 compact 后上下文重建。
  - usage 从流式 `message_delta/message_stop` 或普通响应中累积，作为 session state 的一部分。

## 当前状态

当前仓库已有 MVP：

- `onecode_demo/loop.py` 已实现基本 `while True`、工具调用、Stop hook、rate limit retry、max output recovery、reactive compact。
- `onecode_demo/compaction.py` 已实现工具结果预算、旧工具结果清理、滑动窗口、full compact、reactive compact 和 transcript 写入。
- `onecode_demo/model_client.py` 已把 Chat Completions 响应归一为 `LLMResponse` 和 `ToolCall`。
- `onecode_demo/state.py` 已保存 usage、turn count、reactive compact guard、output recovery count 和 last transition。

主要缺口：

- 没有独立的上下文重建层；压缩和模型可见消息投影直接散在 `AgentLoop`/`Compactor`。
- transcript 只在 full compact 时写；用户消息 accepted 后、assistant/tool result 后还没有持续写入。
- `state.messages` 既是完整会话历史，又是模型可见工作内存；未来恢复、compact boundary、content replacement 难以扩展。
- transition 只是字符串，尚未形成可观测事件或结构化记录。
- 模型客户端目前非流式，但主循环契约应提前以 `response.tool_calls` 作为续轮信号，避免未来流式迁移时改 loop。

## 实现边界

### 主循环职责

`AgentLoop` 只保留这些职责：

1. 接收用户 prompt，触发 `UserPromptSubmit`。
2. 把被接受的用户消息追加到会话状态并记录 transcript。
3. 进入 `while True`。
4. 调用上下文重建层生成本轮 `ContextSnapshot`。
5. 调用模型客户端。
6. 根据响应更新 usage、追加 assistant message、记录 transcript。
7. 如果有 tool calls，交给工具执行器，追加 tool result message，记录 transcript，并继续。
8. 如果没有 tool calls，触发 Stop hook；Stop hook 不要求继续则返回最终文本。
9. 对 rate limit、context limit、max output、max turns 等恢复路径设置 transition。

主循环不直接实现：

- 具体工具名分支。
- 路径权限策略。
- prompt section 文本细节。
- 压缩策略细节。
- provider-specific 字段解析。
- transcript 文件格式细节。

### 上下文重建层

新增 `onecode/context.py`，提供：

```python
@dataclass
class ContextSnapshot:
    system_prompt: str
    messages: list[dict]
    tool_schemas: list[dict]
    transcript_refs: list[str] = field(default_factory=list)
    transition: str | None = None

class ContextBuilder:
    def build_for_model(self, state: AgentState) -> ContextSnapshot:
        ...
```

`ContextBuilder.build_for_model()` 的固定顺序：

1. 从 `state.messages` 或 compact boundary 之后的消息生成候选历史。
2. 调用 `Compactor.prepare_before_model_call()`：
   - `apply_tool_result_budget`
   - `cleanup_old_tool_results`
   - `apply_sliding_window`
   - `full_compact` when usage ratio crosses threshold
3. 使用 `assemble_system_prompt(PromptContext(...))` 组装系统提示词。
4. 使用 `ToolRegistry.api_schemas()` 动态组装模型可见工具。
5. 返回不可变快照给 `AgentLoop`。

关键约束：

- `build_for_model()` 可以更新 `state.messages`，但必须只通过 compactor 或明确的 compact boundary 规则更新。
- tool schema 必须来自当前 registry，不能在 loop 中硬编码。
- prompt 必须按当前 runtime 状态组装，不能缓存过期工具列表。
- 如果未来引入 deny-first 权限，ContextBuilder 必须只暴露未被拒绝的工具。

### Transcript 和会话历史

把 transcript 从“压缩前备份”扩展为“会话恢复来源”：

- 用户 prompt 被 hook 接受后立即写 transcript。
- assistant message 追加后写 transcript。
- tool result message 追加后写 transcript。
- full compact/reactive compact 前继续写完整 transcript。

实现方式：

- 保留现有 `write_transcript(messages, transcript_dir)` 作为 full snapshot 写入。
- 新增 `TranscriptRecorder.append(message_or_messages)`，用于按事件追加 JSONL。
- `AgentState` 增加 `transcript_path: Path | None` 或 `session_id`，让同一会话写入同一个 transcript。
- full compact summary 中保留 transcript 路径，模型可看到可恢复来源。

### 状态结构

扩展 `AgentState`，但保持字段稳定：

```python
@dataclass
class AgentState:
    messages: list[dict]
    usage: UsageSnapshot
    turn_count: int = 0
    has_reactive_compacted: bool = False
    max_output_recovery_count: int = 0
    last_transition: str | None = None
    consecutive_compact_failures: int = 0
    session_id: str | None = None
    transcript_path: Path | None = None
```

暂不把完整 QueryEngine 的所有字段照搬进 Python。以下字段只预留边界：

- `pending_tool_use_summary`
- `read_file_state`
- `permission_denials`
- `compact_generation`
- `content_replacement_index`

### 模型响应契约

`LLMResponse` 继续作为 provider 归一化边界：

- `assistant_message` 是可直接追加进 `state.messages` 的内部消息。
- `tool_calls` 是主循环唯一续轮信号。
- `stop_reason` 只作为恢复和观测辅助字段。
- `usage` 由 `UsageSnapshot.from_usage()` 归一化。
- `output_interrupted` 由 model client 根据 provider 字段判断。

未来流式实现也必须在 `ModelClient` 内部完成：

- 流式时发现 tool use block 就收集到 `tool_calls`。
- `message_delta` 或等价事件中更新最终 `stop_reason`。
- `message_stop` 或最终响应中汇总 usage。
- loop 仍只读取 `LLMResponse`，不解析 provider stream event。

### 工具执行器

从 `AgentLoop._execute_tool_calls()` 抽出 `ToolExecutor`：

```python
class ToolExecutor:
    def execute(self, tool_calls: list[ToolCall], state: AgentState) -> list[dict]:
        ...
```

职责：

- 使用 `partition_tool_calls(tool_calls, registry)` 保留未来并发接口。
- 查找工具、校验输入、触发 `PreToolUse`。
- hook 阻断、未知工具、参数错误、handler 异常都返回 `is_error=True` tool result。
- 执行成功后触发 `PostToolUse`。
- 不决定是否继续主循环。

## 主循环算法

目标伪代码：

```python
def run(prompt: str) -> str:
    accepted = hooks.emit("UserPromptSubmit", prompt=prompt, state=state)
    if accepted.blocked:
        return accepted.message or "Prompt blocked."

    append_user_message(accepted.message or prompt)
    transcript.record(state.messages[-1])

    return run_loop()

def run_loop() -> str:
    while True:
        state.turn_count += 1
        if state.turn_count > config.max_turns:
            state.last_transition = "max_turns"
            return "Stopped: maximum turn count reached."

        snapshot = context_builder.build_for_model(state)

        try:
            response = send_with_rate_limit_retries(snapshot)
        except ContextLimitError:
            if compactor.reactive_compact(state, model_client, hooks):
                state.last_transition = "reactive_compact_retry"
                continue
            return "Stopped: context is still too large after reactive compact."

        update_usage(response.usage)

        if response.output_interrupted or response.stop_reason == "max_tokens":
            if recover_output_limit(response):
                continue
            return response.final_text

        state.messages.append(response.assistant_message)
        transcript.record(response.assistant_message)

        if response.tool_calls:
            result_blocks = tool_executor.execute(response.tool_calls, state)
            tool_result_message = {"role": "user", "content": result_blocks}
            state.messages.append(tool_result_message)
            transcript.record(tool_result_message)
            state.last_transition = "tool_use"
            state.max_output_recovery_count = 0
            state.has_reactive_compacted = False
            continue

        stop_result = hooks.emit("Stop", state=state)
        if stop_result.force_continue and stop_result.message:
            continuation = {"role": "user", "content": stop_result.message}
            state.messages.append(continuation)
            transcript.record(continuation)
            state.last_transition = "stop_hook_continue"
            continue

        state.last_transition = "completed"
        return response.final_text
```

## 错误恢复策略

- `rate_limit_retry`
  - 由 `_send_with_rate_limit_retries()` 保持同一次 snapshot 重试。
  - 不追加部分 assistant message。
  - 使用 `Retry-After` 或指数退避加 jitter。

- `reactive_compact_retry`
  - 捕获 `ContextLimitError` 后最多执行一次 reactive compact。
  - reactive compact 前写 transcript。
  - 重试失败直接停止，不触发 Stop hook。

- `max_output_tokens_escalate`
  - 第一次 max output 中断时提高 `max_output_tokens`，不追加截断输出。

- `max_output_tokens_recovery`
  - 升级后仍中断时，追加截断 assistant message 和 continuation user message。
  - 最多使用 `config.max_output_recovery_retries`。

- `tool_use`
  - 工具错误作为 tool result 回填，让模型修正。
  - 主循环不因单个工具失败崩溃。

- `stop_hook_continue`
  - Stop hook 可以追加一条 user message 继续。
  - 如果 Stop hook 产生 blocking error 后再次遇到 context limit，不重置 reactive compact guard，避免重复压缩循环。

- `max_turns`
  - 达到 turn 上限后直接停止，并记录 transition。

## 实施步骤

1. 新增上下文重建模块
   - 创建 `onecode/context.py`。
   - 定义 `ContextSnapshot` 和 `ContextBuilder`。
   - 把 `assemble_system_prompt()`、`ToolRegistry.api_schemas()`、`Compactor.prepare_before_model_call()` 串到 builder 中。

2. 新增 transcript recorder
   - 扩展 `onecode/transcript.py`。
   - 支持会话级 JSONL append。
   - `AgentState` 保存 `session_id` 和 `transcript_path`。
   - 保留 full compact 使用的 snapshot transcript。

3. 抽出工具执行器
   - 创建 `onecode/tool_executor.py` 或放在 `onecode/tools/executor.py`。
   - 从 `AgentLoop` 移出 `_execute_tool_calls()` 和 `_execute_one_tool()`。
   - 保持现有 hook、validation、截断行为不变。

4. 收敛 `AgentLoop`
   - `AgentLoop` 注入 `ContextBuilder`、`ToolExecutor`、`TranscriptRecorder`。
   - loop 内只保留编排和 transition。
   - 以 `response.tool_calls` 是否为空作为是否继续的判断。

5. 完善 context-limit 和 compact boundary 行为
   - full compact 后生成可识别 compact summary message。
   - reactive compact 后保留 summary 和最近尾部消息。
   - 为未来 `get_messages_after_compact_boundary()` 预留函数。

6. 结构化观测事件
   - 先用 lightweight JSONL trace 或 hook payload 记录：
     - `model_call_start`
     - `model_call_end`
     - `tool_use`
     - `compact_start`
     - `compact_end`
     - `transition`
   - CLI 文本日志继续通过 hook 输出。

7. 测试覆盖
   - 保留现有 `tests/test_loop.py` 和 `tests/test_compaction.py`。
   - 新增 `tests/test_context.py`：
     - builder 按顺序调用 compactor、prompt、tool schema。
     - registry 变化后 tool schema 动态变化。
   - 新增 `tests/test_transcript.py`：
     - 用户消息 accepted 后立即写 transcript。
     - assistant 和 tool result 都写入同一会话 transcript。
   - 更新 loop 测试：
     - 有 `tool_calls` 即继续，即使 `stop_reason` 为空。
     - 没有 `tool_calls` 时停止，即使 `stop_reason` 是非标准值。
     - context limit 后 reactive compact 只重试一次。
     - max output 升级前不污染 messages。

## 验收标准

- 主循环文件中不出现具体工具名分支。
- 模型可见工具只来自 `ToolRegistry.api_schemas()`。
- 每轮模型调用前都会通过 `ContextBuilder` 重建 `ContextSnapshot`。
- 工具调用续轮信号只依赖 `LLMResponse.tool_calls` 是否为空。
- 用户消息、assistant message、tool result message 都写入 session transcript。
- full compact/reactive compact 前写完整 transcript，compact summary 中包含 transcript 路径。
- 上下文超限触发 reactive compact retry；二次失败明确停止。
- 429、max output、tool error、Stop hook continue、max turns 都设置可测试 transition。
- 现有测试通过，并新增 context/transcript/loop 信号测试。

## 暂不实现

- 真正流式输出和 streaming tool executor。
- 并发工具执行。
- session resume UI。
- long-term memory。
- task DAG 和子 Agent。
- 完整 deny-first 权限系统。
- provider-specific prompt cache 或 cache edit。

这些能力只保留接口位置，不能提前侵入主循环。
