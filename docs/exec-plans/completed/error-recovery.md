# OneCode 全局错误处理与恢复机制

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。实现者只阅读本文件和当前工作树，也应能完成 OneCode 错误处理与恢复机制第一版，不需要依赖此前对话。

## Purpose / Big Picture

完成本计划后，OneCode 在模型调用、工具执行、MCP、配置解析、文件系统访问和 CLI 顶层错误上会有统一的错误类型、统一的错误日志和可恢复的模型调用流程。用户看到的变化是：遇到 429、5xx、网络抖动或 provider 过载时，CLI 不再立即崩溃，而是按指数退避重试；遇到 context limit 时，runtime 触发 reactive compact 后重试；遇到模型输出被截断时，runtime 先提升输出 token 预算，再用 continuation prompt 继续；遇到不可恢复错误时，错误会出现在 `.onecode/<session_id>/errors.jsonl`，同时 trace 中仍保留精简的 transition 和 span 事实。

这个计划同时解决技术债 `TD-004` 的主体问题：恢复类 transition 已经存在，但 provider 和工具错误仍可能绕过 loop 恢复流程。第一版不实现 529 三连后的 fallback model switch；fallback 需要 provider factory 和 model config 的独立设计，本计划只预留接口，不改变当前 `.env` 中配置的模型。

## Progress

- [x] (2026-06-07 +08:00) 阅读 `AGENTS.md`、`PLANS.md`、`architecture.md`、相关 design docs、active exec plan、tech debt tracker，以及 `docs/references/s11_error_recovery/` 中的 README、`errors.ts`、`errorLogSink.ts` 和 `services/withRetry.ts`。
- [x] (2026-06-07 +08:00) 与用户确认范围：全局错误都纳入；新增统一错误注册表；retry 机制统一；retry 参数按参考实现；暂不实现 fallback model；max-output recovery 按本计划的一阶段方案；streaming recovery 按参考实现隐藏可恢复失败期间的 partial deltas；错误日志放在现有 `services/observability/` 内。
- [x] (2026-06-07 +08:00) 撰写本中文 ExecPlan，明确模块落点、接口、实现顺序、测试策略和验收方式。
- [x] (2026-06-07 +08:00) 新增 `services/errors.py` 统一错误注册表、基础错误类型和通用 helper；`ProviderError` 已继承 `OneCodeError` 并保留兼容字段，MCP/config/shell/tool 等异常可通过 `onecode_error_details()` 分类。
- [x] (2026-06-07 +08:00) 在 `services/observability/error_log.py` 新增独立错误日志 sink 和 recorder，写入 `.onecode/<session_id>/errors.jsonl`，并复用 attributes sanitizer。
- [x] (2026-06-07 +08:00) 新增 `services/model/retry.py`，实现 provider-neutral retry policy、指数退避、jitter、retry exhaustion 和 streaming attempt buffering。
- [x] (2026-06-07 +08:00) 改造 `core/loop.py` 的模型调用恢复流程，接入 retry runner、reactive compact、max-output escalation 和 continuation prompt。
- [x] (2026-06-07 +08:00) 扩展 OpenAI-compatible provider adapter，让 `finish_reason="length"`、`max_tokens` 和 `max_output_tokens` 映射为 `output_interrupted=True`，并支持 `usage_hints.request_overrides.max_output_tokens` 到 `max_tokens` 的投影。
- [x] (2026-06-07 +08:00) 接入 CLI runtime、MCP manager、工具 executor 和 CLI 主循环普通 exception catch 的错误日志记录；`/status` 显示 errors log 路径。
- [x] (2026-06-07 +08:00) 完成 compileall、全量 pytest 和技术债状态更新；`uv run python -m pytest tests -q` 输出 `322 passed in 6.50s`。

## Surprises & Discoveries

- Observation: OneCode 当前不是完全没有错误恢复；`core/loop.py` 已经捕获 `ProviderError`，并且对 `error_type="context_limit_exceeded"` 调用一次 reactive compact。
  Evidence: `core/loop.py::_try_reactive_compact()` 会设置 `TransitionReason.REACTIVE_COMPACT_RETRY`，调用 `compaction_service.reactive_compact()`，并在成功后继续主循环。

- Observation: Provider-neutral 错误的第一层已经存在，但还不是全局错误注册表。
  Evidence: `services/model/types.py::ProviderError` 保存 `provider_id`、`status_code`、`error_type` 和 `retryable`；`infrastructure/providers/http.py::provider_error_from_http_status()` 已将 429、5xx、413/context limit 映射为 provider-neutral 错误。

- Observation: 现有 trace sink 不适合直接承载完整错误日志。
  Evidence: `services/observability/sinks.py::JsonlTraceSink` 写 `.onecode/<session_id>/trace.jsonl`，`services/observability/sanitize.py` 会清洗 prompt、content、stdout 等字段；trace 当前用于 `/trace` 和 runtime span/event，不应塞入完整 stack 或 MCP debug log。

- Observation: 要严格按参考实现隐藏可恢复 streaming 错误，第一版必须缓冲单次模型 attempt 的 `ModelStreamEvent`，直到该 attempt 成功完成后才向 CLI flush。
  Evidence: 当前 `core/loop.py` 一收到 `content_delta` 就 yield `AgentEvent(type="assistant_delta")`；如果 provider 中途抛出 retryable error，用户已经看到了后续会被丢弃的 partial text。缓冲 attempt 是第一版最直接、可测试的修复。

- Observation: 为了同时隐藏 retry 失败 partial delta 和 max-output escalation 的截断 delta，`core/loop.py` 也需要在成功 attempt 释放后先看完整 `message_completed`，再决定是否向 UI flush content delta。
  Evidence: `tests/test_loop.py::test_loop_retries_retryable_provider_error_and_hides_partial_delta` 和 `tests/test_loop.py::test_loop_escalates_max_output_tokens_before_persisting_truncated_output` 均断言失败/截断文本不会进入 UI delta 或 message store。

## Decision Log

- Decision: 统一错误注册表放在 `services/errors.py`，而不是顶层 `utils/errors.py`。
  Rationale: OneCode 是 Python runtime，当前架构没有通用 `utils/` 目录；`services/` 是 provider-neutral runtime service 边界。`services/errors.py` 可以被 `core/`、`services/`、`tools/` 和 `infrastructure/` 依赖，同时不能反向 import 这些具体模块，避免循环依赖。
  Date/Author: 2026-06-07 / Codex

- Decision: `ProviderError` 保留在 `services/model/types.py`，但改为继承统一错误基类，并把 provider-specific metadata 保持在 model service 边界内。
  Rationale: `ProviderError` 已被 `core/loop.py`、`infrastructure/config/env.py`、provider adapter 和 tests 广泛使用。直接搬到 `services/errors.py` 会造成不必要 churn；让它继承 `OneCodeError` 能获得统一日志和 helper 行为，同时保留 model 模块的清晰职责。
  Date/Author: 2026-06-07 / Codex

- Decision: 错误日志能力放在 `services/observability/` 内，但实现为独立 `ErrorLogRecorder` 和 `JsonlErrorLogSink`，不复用 trace record 格式。
  Rationale: 用户确认可以与 observability 合并。trace 是 runtime 事实摘要，error log 是排查错误的完整证据；两者共享 session 和 sanitizer，但文件、schema 和调用入口分开，避免 `/trace` 输出 stack 或敏感上下文。
  Date/Author: 2026-06-07 / Codex

- Decision: 第一版 retry 参数采用参考实现：默认最多 10 次 retry，基础延迟 500ms，指数退避上限 32s，jitter 为 base delay 的 0 到 25%，优先尊重 provider 的 retry-after 信息。
  Rationale: 用户明确选择“按照”。这些参数是成熟的保守默认值，足以覆盖 429、5xx、网络错误和 provider overloaded，而不会无限等待。
  Date/Author: 2026-06-07 / Codex

- Decision: 暂不实现 fallback model switch。
  Rationale: 用户明确说“先不实现”。本计划只记录 529/server overload 的连续计数和 retry exhaustion，为未来 `ONECODE_FALLBACK_MODEL` 或 provider catalog fallback 留接口。
  Date/Author: 2026-06-07 / Codex

- Decision: 第一版 streaming recovery 通过 attempt buffering 实现：可恢复 attempt 成功前不向外 emit assistant delta、tool call ready 或 assistant completed。
  Rationale: 用户明确要求按参考实现。当前 loop 是直接 streaming 给 CLI，无法在中途 retry 时撤回 partial output。缓冲每个 attempt 能保证 recoverable error 不污染 UI、message store 或 transcript。
  Date/Author: 2026-06-07 / Codex

- Decision: max-output escalation 通过 provider-neutral request override 实现，不让 `core/loop.py` import 具体 provider config。
  Rationale: `core/` 不能依赖具体 provider。`ContextSnapshot.usage_hints` 已经是 provider 调用前可见的中立扩展字段；实现时可让 `ContextEngine` 从 `RuntimeState.metadata["model_request_overrides"]` 复制 `max_output_tokens` 到 `usage_hints`，provider adapter 再把它投影为 OpenAI-compatible payload 的 `max_tokens`。
  Date/Author: 2026-06-07 / Codex

## Outcomes & Retrospective

第一版实现已落地：统一错误类型、独立 errors JSONL、provider retry runner、loop 的 retry/context-limit/max-output recovery、OpenAI-compatible output interruption 映射、CLI/MCP/tool executor 错误日志接入，以及相关设计文档更新。已通过 `uv run python -m compileall core services infrastructure tools ui` 和 `uv run python -m pytest tests -q`，全量测试结果为 `322 passed in 6.50s`。`TD-004` 已归档为已解决。保留限制：第一版仍不实现 fallback model switch；streaming attempt buffering 会让 CLI 在一次模型 attempt 完成后才看到 delta，这是为了保证可恢复失败和 max-output escalation 不外显截断文本。

## Context and Orientation

OneCode 是 Python code agent runtime。`core/loop.py` 是薄主循环，只负责编排用户输入、构建模型上下文、调用模型、执行工具、写回消息和设置 transition。transition 是 runtime 状态变化的中立名称，定义在 `core/transitions.py`，当前已有 `rate_limit_retry`、`reactive_compact_retry`、`max_output_tokens_escalate` 和 `max_output_tokens_recovery`，但 retry 和 max-output recovery 尚未完整实现。

模型调用协议在 `services/model/`。`services/model/client.py` 定义 `ModelClient.stream(snapshot)` 协议；`services/model/stream.py` 定义 `ModelStreamEvent`，包括 `content_delta`、`tool_call_completed` 和 `message_completed`；`services/model/types.py` 定义 `ProviderError` 和 `ModelUsage`。Provider adapter 位于 `infrastructure/providers/`，当前主要实现是 `infrastructure/providers/chat_completions.py::OpenAICompatibleChatCompletionsClient`。HTTP 错误分类位于 `infrastructure/providers/http.py`。

上下文快照定义在 `services/context/snapshot.py::ContextSnapshot`。它包含 `system_prompt`、`messages`、`tool_schemas`、`usage_hints`、`transcript_refs` 和 `transition`。`usage_hints` 是 provider-neutral 的提示字段，意思是“这些信息可以影响 provider 请求，但不能包含 provider 私有 wire 字段”。本计划会用它承载 max-output request override。

可观测性在 `services/observability/`。`TraceRecorder` 负责结构化 trace，`JsonlTraceSink` 写 `.onecode/<session_id>/trace.jsonl`，CLI 的 `/trace` 会读取 trace 摘要。错误日志和 trace 不同：错误日志应保存完整但经过清洗的 stack、错误类别和上下文，帮助排查不可恢复错误；trace 应继续保存短小的 runtime 事件和 span。

Compaction 在 `services/compaction/`。`ContextCompactionService.reactive_compact(state, error=...)` 能在 provider 报 context limit 后压缩当前消息链，然后让 loop 重试。当前 `core/loop.py` 已有一次 reactive compact 的入口，本计划应保留这个行为，并让它与 retry runner 组合，而不是重复实现 compaction。

工具执行在 `services/tools/executor.py`。工具 handler 和 classifier 的异常已经会被转换成 `ToolExecutionResult(is_error=True)` 的情况较多，但工具 executor、MCP manager、CLI 顶层仍需要把异常写入 error log。错误日志记录不能改变 deny-first 安全边界，也不能把用户拒绝、guard deny 或工具返回的 recoverable tool error 错当作 Python crash。

MCP 能力在 `services/mcp/`。MCP 配置错误类型 `McpConfigError` 已存在于 `services/mcp/config.py`；MCP 连接和工具调用在 `services/mcp/manager.py`。本计划不要求实现 MCP 协议重试，只要求 MCP 配置、连接和工具调用异常能通过统一错误 helper 和 observability error log 记录。

## Plan of Work

第一步是新增统一错误注册表。创建 `services/errors.py`。该文件必须是低层模块，只 import Python 标准库，不 import `core`、`services.model`、`services.observability`、`tools` 或 `infrastructure`。定义 `ErrorSeverity`、`ErrorCategory` 或等价的 `StrEnum`，覆盖至少这些类别：`abort`、`configuration`、`provider`、`network`、`rate_limit`、`context_limit`、`invalid_response`、`filesystem`、`shell`、`mcp`、`tool`、`permission`、`internal`。定义 `OneCodeError(Exception)`，构造参数包含 `message`、`category`、`retryable=False`、`metadata=None` 和可选 `safe_message`。`safe_message` 是可以进入 telemetry 或 trace 的短消息，不能包含完整 prompt、源码、stdout、API key 或绝对路径。

同一文件中定义常用错误类和 helper。第一版至少包含 `AbortError`、`ConfigParseError`、`ShellError`、`McpOperationError`、`ToolRuntimeError` 和 `RetryExhaustedError`，以及 `to_error(value)`、`error_message(value)`、`short_error_stack(value, max_frames=5)`、`errno_code(value)`、`errno_path(value)`、`is_fs_inaccessible(value)`、`is_abort_error(value)`、`onecode_error_details(value)`。`is_fs_inaccessible()` 要覆盖 Python 文件系统常见异常：`FileNotFoundError`、`PermissionError`、`NotADirectoryError`、以及带 `errno` 为 `ENOENT`、`EACCES`、`EPERM`、`ENOTDIR`、`ELOOP` 的 `OSError`。`is_abort_error()` 要识别 `AbortError` 和 `asyncio.CancelledError`，但 loop 顶层不得吞掉 `KeyboardInterrupt`。

第二步是让现有错误接入注册表，但保持兼容。修改 `services/model/types.py::ProviderError` 让它继承 `OneCodeError`，同时继续暴露现有字段 `message`、`provider_id`、`status_code`、`error_type` 和 `retryable`，保证现有 tests 不需要大面积改写。`ProviderError` 的 `category` 应由 `error_type` 映射：`rate_limit_error` 映射到 `rate_limit`，`context_limit_exceeded` 映射到 `context_limit`，`network_error` 映射到 `network`，`configuration_error` 映射到 `configuration`，其他 provider 错误映射到 `provider` 或 `invalid_response`。后续可以让 `services/mcp/config.py::McpConfigError`、`services/tasks/store.py::TaskStoreError` 和 shell runner 错误继承 `OneCodeError`，但第一版只要求 provider 和新增错误类完全接入，其他模块至少通过 `onecode_error_details()` 能被分类为 `mcp`、`filesystem`、`shell` 或 `internal`。

第三步是在 `services/observability/` 内新增错误日志。创建 `services/observability/error_log.py`，定义 `ErrorLogSink` protocol、`NoopErrorLogSink`、`JsonlErrorLogSink` 和 `ErrorLogRecorder`。`JsonlErrorLogSink` 应采用与 `JsonlTraceSink` 相似的缓冲写入、`flush()`、`switch_session(session_id)` 和 `atexit` 清理模式，默认写入 `.onecode/<session_id>/errors.jsonl`。`ErrorLogRecorder.record_error(error, *, source, attributes=None)` 写入 JSON 对象，字段至少包括 `timestamp`、`session_id`、`source`、`category`、`error_type`、`message`、`safe_message`、`retryable`、`stack`、`attributes`。`record_mcp_error(server_name, error, attributes=None)` 也写入同一个 session errors file，但 attributes 中包含 `mcp_server`；第一版不必单独创建每个 server 的日志文件。

错误日志必须复用 `services/observability/sanitize.py::sanitize_attributes()` 清洗 attributes。`message` 和 `stack` 也要经过轻量清洗：API key、Authorization、Bearer token、长 prompt、长源码和超长 stdout/stderr 不能原样写入。第一版可以采用明确规则：超过 4000 字符的 stack 截断；包含 `sk-` 或 `Bearer ` 的片段替换为 `[redacted]`；绝对 workspace 内路径可转为相对路径；workspace 外路径只保留文件名或后缀。不要把 error log 展示接入 `/trace`，但 `/status` 可以显示 error log path。

第四步是新增统一 retry engine。创建 `services/model/retry.py`。定义 `RetryPolicy` dataclass，默认字段为 `max_retries=10`、`base_delay_seconds=0.5`、`max_delay_seconds=32.0`、`jitter_ratio=0.25`。定义 `RetryDecision` 或等价数据结构，包含 `should_retry`、`delay_seconds`、`attempt`、`max_retries`、`transition` 和 `reason`。定义 `retry_delay_seconds(attempt, retry_after_seconds=None, policy=...)`，公式为 `min(0.5 * 2 ** (attempt - 1), 32.0) + random(0, 0.25 * base)`，如果 provider 给了 retry-after，则优先使用 retry-after。测试中必须允许注入 deterministic random 函数或 jitter=0，避免 flaky。

`services/model/retry.py` 还要定义 `ModelRetryRunner`。它接收 `policy`、`sleep`、`trace_recorder` 和可选 `error_log_recorder`。核心方法建议为 `async def stream(self, operation, *, on_retry=None) -> AsyncIterator[ModelStreamEvent]`，其中 `operation()` 返回一个新的 async iterator。每次 attempt 都必须调用新的 `operation()`，因为 provider stream 不能复用。runner 在 attempt 内把所有 `ModelStreamEvent` 放入本地 buffer；只有 attempt 成功完整结束后才把 buffer 里的事件按原顺序 yield 出去。若 attempt 抛出 retryable `ProviderError`，runner 丢弃 buffer，记录 trace 和 error log，调用 `on_retry(error, decision)`，等待 delay 后开始下一次 attempt。若 retry 次数耗尽，抛出 `RetryExhaustedError`，其 `__cause__` 是最后一个 provider error。

retryable 判断第一版遵循 provider-neutral 字段：`ProviderError.retryable is True` 且 `error_type != "context_limit_exceeded"` 才走普通 retry。`context_limit_exceeded` 不在 retry runner 内 compact；它应返回给 loop 的 recovery 流程处理。`authentication_error`、`configuration_error`、`invalid_tool_arguments` 和 `invalid_response` 默认不可 retry。网络错误、429 和 5xx 已由 HTTP 层标记 `retryable=True`，因此不需要 retry runner 知道 HTTP 细节。

第五步是改造 `core/loop.py` 的模型调用阶段。`AgentLoop.__init__()` 增加可选 `model_retry_runner: ModelRetryRunner | None = None` 和 `error_log_recorder: ErrorLogRecorder | None = None` 参数；默认使用 no-op error log 和默认 retry runner，或者保持 None 时行为与 tests 容易兼容。`_run_loop_async()` 中模型调用不再直接 `async for model_event in self.model_client.stream(snapshot)`，而是调用 retry runner 包裹的 operation。`on_retry` 回调要设置 `RuntimeState.last_transition = TransitionReason.RATE_LIMIT_RETRY`，调用 `_record_transition()`，记录 `model_retry` trace event，并让外层 loop 在恢复后向 UI yield `AgentEvent(type="transition", transition="rate_limit_retry", metadata={...})`。由于 retry runner 本身是 async iterator，推荐在 loop 中收集 retry transitions 到一个 list，在成功 flush model events 前先 yield transition event，或者让 `on_retry` append pending agent events。

`core/loop.py` 仍然负责 context-limit recovery。若 retry runner 或 provider 抛出 `ProviderError(error_type="context_limit_exceeded")`，现有 `_try_reactive_compact()` 逻辑继续运行：第一次 compact 成功后 yield `reactive_compact_retry` transition 并 `continue` 进入下一轮同一用户任务；第二次仍失败则记录 error log 并抛出。若抛出 `RetryExhaustedError` 或其他 `OneCodeError`，loop 记录 error log 后抛出给 CLI 顶层渲染；不要把不可恢复 provider error 追加到 message store。

第六步是实现 max-output recovery。先修改 `infrastructure/providers/chat_completions.py`：当 OpenAI-compatible streaming 返回 `finish_reason` 为 `length`、`max_tokens` 或其他明确代表输出 token 用尽的值时，最终 `ModelStreamEvent.message_completed()` 应设置 `output_interrupted=True`，`stop_reason` 保持 provider 原始值。相关 helper 可命名为 `_is_output_interrupted_stop_reason(stop_reason) -> bool`。补充 provider adapter 测试，证明 `finish_reason="length"` 会变成 `output_interrupted=True`。

然后修改 `RuntimeState`。新增 `has_escalated_max_output_tokens: bool = False` 或等价 metadata，但推荐显式字段，和已有 `max_output_recovery_count` 放在一起。`start_new_session()` 必须重置该字段。定义常量：`ESCALATED_MAX_OUTPUT_TOKENS = 64000`、`MAX_OUTPUT_RECOVERY_RETRIES = 3`，以及 continuation prompt：

    Output token limit hit. Resume directly; no apology, no recap of what you were doing. Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces.

如果 `completed_message.output_interrupted` 为 true，loop 必须在追加 assistant message、触发 assistant completed hook 或执行工具之前处理。第一次遇到 interrupted 时，不追加截断 assistant，不向 UI emit assistant delta 或 completed，设置 `state.has_escalated_max_output_tokens = True`，设置 transition `max_output_tokens_escalate`，把 `state.metadata["model_request_overrides"]["max_output_tokens"] = 64000`，yield transition，然后 `continue` 重新构建 snapshot 并重试同一消息链。第二次及之后仍 interrupted 时，追加截断 assistant message 和一个 user continuation prompt，递增 `state.max_output_recovery_count`，设置 transition `max_output_tokens_recovery`，yield transition，然后继续。达到 3 次 continuation 后，停止 recovery，追加最后一次 assistant message，向用户返回当前 final text，并记录 error log 或 trace event 表示 recovery exhausted。

为了让 provider 看到 output token override，修改 `core/context_engine.py` 或 compaction preparer 的组合点。推荐在 `ContextEngine.build_for_model()` 中读取 `state.metadata.get("model_request_overrides")`，将其中安全字段复制到 `ContextSnapshot.usage_hints["request_overrides"]`。然后修改 `infrastructure/providers/chat_completions.py::_build_payload()`，当 `snapshot.usage_hints["request_overrides"]["max_output_tokens"]` 是正整数时，把 payload 的 `max_tokens` 设置为该值。这个字段名保持 provider-neutral；OpenAI-compatible adapter 自己负责把它映射成 `max_tokens`。

第七步是接入 CLI 和 runtime composition。修改 `ui/cli/types.py::CliRuntime` 增加 `error_log_recorder` 字段，默认 no-op。修改 `ui/cli/app.py::build_runtime()` 创建 `JsonlErrorLogSink(workspace / ".onecode", state.session_id)` 和 `ErrorLogRecorder(session_id=state.session_id, workspace=workspace, sink=...)`，传给 `AgentLoop`、`ModelRetryRunner`、`McpConnectionManager` 和其他需要记录错误的组件。`CliRuntime.with_session()` 必须在 `/resume` 和 `/clear` 后调用 `error_log_recorder.switch_session(new_session_id)`，与 trace recorder 保持一致。修改 `ui/cli/renderer.py::render_status()`，在 status 中显示 `errors: .onecode/<session_id>/errors.jsonl`，如果是 no-op 则显示 disabled 或省略。

CLI 顶层 catch 要记录错误。`ui/cli/app.py::main_loop_async()` 中捕获普通 `Exception` 时，在 `print(renderer.render_error(str(exc)))` 前调用 `runtime.error_log_recorder.record_error(exc, source="cli_main_loop")`。构建 runtime 时如果 provider 配置错误导致 `build_runtime()` 抛出，而 recorder 还没创建，第一版可以只打印错误，不要求写入 session error log，因为 session 尚不存在；后续可加 bootstrap error log。

第八步是接入 MCP 和工具错误日志。修改 `services/mcp/manager.py` 构造函数，增加 `error_log_recorder: ErrorLogRecorder | None = None`，默认 no-op。连接失败、server 调用失败和 close 失败时，在保持现有异常语义的前提下调用 `record_mcp_error(server_name, exc, attributes={...})`。修改 `services/tools/executor.py` 构造函数，增加 `error_log_recorder`。当 descriptor lookup、schema validation、classification、guard、permission、hook 或 handler 抛出未预期异常并被转换为 `ToolExecutionResult(is_error=True)` 时，记录 `source="tool_executor"`，attributes 包含 `tool_name`、`tool_call_id`、`stage`，但不能记录完整 tool input 或 tool output。

第九步是更新文档和技术债。修改 `docs/design-docs/model-and-infrastructure-architecture.md`，说明 `services/model/retry.py` 是 provider-neutral retry engine，context-limit 仍由 loop/compaction 处理。修改 `docs/design-docs/observability-and-cli-architecture.md`，说明 trace 与 error log 的区别、路径和 CLI status 展示。修改 `docs/design-docs/core-runtime-architecture.md`，说明 `rate_limit_retry`、`reactive_compact_retry`、`max_output_tokens_escalate` 和 `max_output_tokens_recovery` 的实际行为。最后更新 `docs/tech-debt/tech-debt-tracker.md`：如果本计划全部完成，`TD-004` 可移到已解决；如果只完成 provider retry 和 error log，但未完成 max-output recovery，则标为部分缓解并说明剩余缺口。

## Concrete Steps

从仓库根目录 `D:\study\OneCode` 开始。不要先运行 destructive git 命令；当前工作树可能包含用户或其他任务的未提交改动。先查看状态：

    git status --short

新增错误注册表后，运行局部测试：

    uv run python -m pytest tests/test_errors.py -q

实现 observability error log 后，运行：

    uv run python -m pytest tests/test_observability_error_log.py tests/test_observability_trace.py -q

实现 retry engine 后，运行：

    uv run python -m pytest tests/test_model_retry.py -q

接入 loop recovery 后，运行：

    uv run python -m pytest tests/test_loop.py tests/test_async_loop.py -q

改 provider adapter 后，运行：

    uv run python -m pytest tests/test_openai_compatible_provider.py tests/test_openai_compatible_provider_streaming.py -q

接入 CLI、MCP 和工具 executor 后，运行：

    uv run python -m pytest tests/test_cli_commands.py tests/test_cli_resume.py tests/test_tool_registry_and_executor.py -q

最后运行 compile 和全量测试：

    uv run python -m compileall core services infrastructure tools ui
    uv run python -m pytest tests -q

如果全量测试数量随其他 active work 改变，不要在计划里硬编码最终 passed 数；实现者应在 `Outcomes & Retrospective` 记录实际输出，例如：

    312 passed in 18.42s

## Validation and Acceptance

第一条验收是 retryable provider error 不再让 loop 立即失败。新增测试应构造一个 fake model client：第一次 `stream(snapshot)` 抛出 `ProviderError("rate limited", error_type="rate_limit_error", status_code=429, retryable=True)`，第二次返回成功的 `message_completed`。运行 `uv run python -m pytest tests/test_loop.py -q` 后，应看到新测试通过，并断言最终文本是第二次返回的内容，trace 中出现 `rate_limit_retry` transition，message store 只包含一次最终 assistant message，不包含失败 attempt 的 partial output。

第二条验收是 streaming partial output 被隐藏。新增测试构造 fake model client：第一次 attempt 先 yield `content_delta("partial")`，随后抛出 retryable provider error；第二次 attempt 返回 `content_delta("final")` 和成功 completed。消费 `AgentLoop.stream()` 时，不应看到 `"partial"` 的 assistant delta，只应看到 `"final"`。这证明参考实现式 withheld 行为已经生效。

第三条验收是 context-limit 仍走 reactive compact，而不是普通 retry。已有 `tests/test_loop.py::test_loop_reactive_compacts_once_after_context_limit` 应继续通过，并补充 trace/error log 断言：`context_limit_exceeded` 第一次触发 `reactive_compact_retry` transition；如果第二次仍 context limit，loop 记录 error log 并抛出，不无限 compact。

第四条验收是 max-output escalation 工作。新增测试构造 fake model client：第一次返回 `message_completed(output_interrupted=True, final_text="cut")`，第二次返回成功 `final_text="complete"`。断言第一次截断 assistant 没有写入 message store，`state.has_escalated_max_output_tokens is True`，第二次 snapshot 的 `usage_hints["request_overrides"]["max_output_tokens"] == 64000`，最终返回 `"complete"`。

第五条验收是 max-output continuation 工作。新增测试构造 fake model client：第一次 interrupted 触发 escalation，第二次仍 interrupted，第三次成功。断言第二次截断 assistant 和 continuation user prompt 被写入 message store，`state.max_output_recovery_count == 1`，最终成功文本返回给用户。再补一个 exhaustion 测试，连续超过 3 次 continuation 后 loop 停止 recovery 并返回最后 partial text。

第六条验收是错误日志写入磁盘且经过清洗。新增 `tests/test_observability_error_log.py`：创建 `JsonlErrorLogSink(tmp_path / ".onecode", "session-x")`，记录一个带 API key、workspace 内路径和长 stack 的异常，flush 后读取 `.onecode/session-x/errors.jsonl`。断言 JSONL 存在，包含 `timestamp`、`session_id`、`source`、`category` 和 `stack`，但不包含原始 API key，不包含超长未截断字符串。

第七条验收是 CLI status 能展示 error log path。更新 CLI command 测试，让 `/status` 输出中包含 `errors:` 或等价字段，路径指向当前 session 的 `errors.jsonl`。

## Idempotence and Recovery

本计划的代码改动应保持可重复运行。新增 JSONL sink 只追加日志，不删除已有 `.onecode` 内容；测试必须使用 `tmp_path`，不能写真实 workspace 的 `.onecode`。retry tests 必须注入 no-op sleep 或 fake sleep，不能真的等待 32 秒。随机 jitter 必须可控，测试中使用 jitter_ratio=0 或注入固定 random。

如果某一步失败，先运行对应局部测试定位，不要回滚无关文件。由于工作树可能已有其他任务的未提交改动，禁止使用 `git reset --hard` 或 `git checkout -- .`。如果新文件命名或接口需要调整，更新本 ExecPlan 的 `Decision Log` 和 `Progress`，再修改代码。若 implementation 中发现某个异常不能继承 `OneCodeError` 而不破坏兼容性，保留原异常类，并通过 `onecode_error_details()` 做分类；同时在 `Surprises & Discoveries` 写明证据。

## Artifacts and Notes

参考实现位于 `docs/references/s11_error_recovery/`。本计划吸收这些行为而不是照搬 TypeScript 文件结构：`errors.ts` 对应 OneCode 的 `services/errors.py`；`errorLogSink.ts` 对应 `services/observability/error_log.py`；`services/withRetry.ts` 对应 `services/model/retry.py`。OneCode 是 Python 项目，模块边界以 `architecture.md` 和 `docs/design-docs/` 为准。

关键 transition 名称已经在 `core/transitions.py` 中存在：

    rate_limit_retry
    reactive_compact_retry
    max_output_tokens_escalate
    max_output_tokens_recovery

第一版不要新增 fallback transition。若未来实现 fallback model，应新增独立计划，因为它涉及 provider config、runtime state、CLI 通知和 model client rebuild。

建议新增测试文件：

    tests/test_errors.py
    tests/test_observability_error_log.py
    tests/test_model_retry.py

建议扩展现有测试文件：

    tests/test_loop.py
    tests/test_openai_compatible_provider.py
    tests/test_openai_compatible_provider_streaming.py
    tests/test_cli_commands.py
    tests/test_cli_resume.py
    tests/test_tool_registry_and_executor.py

## Interfaces and Dependencies

在 `services/errors.py` 中定义：

    class ErrorCategory(StrEnum): ...

    class OneCodeError(Exception):
        def __init__(
            self,
            message: str,
            *,
            category: ErrorCategory | str,
            retryable: bool = False,
            safe_message: str | None = None,
            metadata: Mapping[str, Any] | None = None,
        ) -> None: ...

    class AbortError(OneCodeError): ...
    class ConfigParseError(OneCodeError): ...
    class ShellError(OneCodeError): ...
    class McpOperationError(OneCodeError): ...
    class ToolRuntimeError(OneCodeError): ...
    class RetryExhaustedError(OneCodeError): ...

    def to_error(value: object) -> BaseException: ...
    def error_message(value: object) -> str: ...
    def short_error_stack(value: object, max_frames: int = 5) -> str: ...
    def is_abort_error(value: object) -> bool: ...
    def is_fs_inaccessible(value: object) -> bool: ...
    def onecode_error_details(value: object) -> ErrorDetails: ...

在 `services/observability/error_log.py` 中定义：

    class ErrorLogSink(Protocol):
        def emit(self, record: Mapping[str, Any]) -> None: ...
        def flush(self) -> None: ...

    class JsonlErrorLogSink:
        def __init__(self, root_dir: Path, session_id: str, *, flush_interval_seconds: float = 1.0) -> None: ...
        @property
        def error_log_path(self) -> Path: ...
        def switch_session(self, session_id: str) -> None: ...
        def emit(self, record: Mapping[str, Any]) -> None: ...
        def flush(self) -> None: ...

    class ErrorLogRecorder:
        @classmethod
        def noop(cls, session_id: str | None = None) -> ErrorLogRecorder: ...
        def switch_session(self, session_id: str) -> None: ...
        def record_error(self, error: object, *, source: str, attributes: Mapping[str, Any] | None = None) -> None: ...
        def record_mcp_error(self, server_name: str, error: object, attributes: Mapping[str, Any] | None = None) -> None: ...

在 `services/model/retry.py` 中定义：

    @dataclass(frozen=True)
    class RetryPolicy:
        max_retries: int = 10
        base_delay_seconds: float = 0.5
        max_delay_seconds: float = 32.0
        jitter_ratio: float = 0.25

    @dataclass(frozen=True)
    class RetryDecision:
        attempt: int
        max_retries: int
        delay_seconds: float
        transition: str
        reason: str

    def retry_delay_seconds(
        attempt: int,
        *,
        retry_after_seconds: float | None = None,
        policy: RetryPolicy = RetryPolicy(),
        random_fraction: Callable[[], float] | None = None,
    ) -> float: ...

    class ModelRetryRunner:
        async def stream(
            self,
            operation: Callable[[], AsyncIterator[ModelStreamEvent]],
            *,
            on_retry: Callable[[ProviderError, RetryDecision], Awaitable[None] | None] | None = None,
        ) -> AsyncIterator[ModelStreamEvent]: ...

`ProviderError` should keep this compatible constructor in `services/model/types.py`:

    class ProviderError(OneCodeError):
        def __init__(
            self,
            message: str,
            *,
            provider_id: str | None = None,
            status_code: int | None = None,
            error_type: str | None = None,
            retryable: bool = False,
            retry_after_seconds: float | None = None,
        ) -> None: ...

`AgentLoop.__init__()` should accept:

    model_retry_runner: ModelRetryRunner | None = None
    error_log_recorder: ErrorLogRecorder | None = None

Do not make `core/loop.py` depend on concrete provider adapter classes. Do not make `services/errors.py` import observability, model, MCP or tools modules. Do not place provider-specific HTTP status logic in the loop; HTTP status mapping stays in `infrastructure/providers/http.py`.

## Change Notes

- 2026-06-07 / Codex: Initial ExecPlan created after user confirmed scope. The plan records the choice to keep error logging inside `services/observability/`, to implement global error helpers in `services/errors.py`, to add unified retry in `services/model/retry.py`, to defer fallback model switching, and to buffer streaming attempts so recoverable partial output is not shown or persisted.
- 2026-06-07 / Codex: Implemented the first recovery pass. The implementation keeps retry in `services/model/retry.py`, error logs in `services/observability/error_log.py`, and max-output recovery in `core/loop.py`; it intentionally buffers successful attempt events in the loop until `message_completed` is known so output-interrupted content can be suppressed before UI flush or message persistence.
