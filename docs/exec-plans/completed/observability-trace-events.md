# 实现本地优先的结构化 Observability Trace

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

本计划遵守仓库根目录的 `PLANS.md`。本文是一个完整、自包含的实现说明；执行者只需要当前工作树和本文件，就能完成、验证并维护这项变更。

## Purpose / Big Picture

OneCode 当前已经能运行一个真实的 code agent 循环，但用户和开发者只能看到最终文本、少量 CLI 输出和 transcript。模型调用花了多久、工具为什么被阻止、权限等待耗时多少、一次任务经历了哪些 transition，目前都没有结构化事实来源。完成本计划后，每个会话会在 `.onecode/<session_id>/trace.jsonl` 中留下本地 JSONL trace。开发者可以打开这个文件，看到一次用户交互中的 `interaction`、`context_prepare`、`model_call`、`tool_batch`、`tool_call`、`permission_wait`、`hook` 和 `transition` 事件，并且这些事件不会记录源码全文、prompt 全文、工具输出全文、API key 或完整外部路径。

这项变更的可见效果很直接。运行测试后，新增测试会证明 trace 文件里出现按父子关系关联的 span 和事件。启动 CLI 执行一个触发工具调用的请求后，`.onecode/<session_id>/trace.jsonl` 会出现本次运行的结构化记录。执行 `/status` 时，CLI 会显示当前 trace 文件路径和最近一次 transition；执行新增的 `/trace` 命令时，CLI 会以简短文本展示最近若干条 trace event，方便不用手动打开 JSONL 文件也能检查 agent 做了什么。

本计划借鉴用户提供的 observability 参考架构中的几个适合本地排障的思想：统一事件入口、本地 trace sink、Interaction/LLM/Tool/Permission/Hook span 层级、debug/trace/transcript 分离、metadata 隐私清洗和缓冲写入。OneCode 第一版只服务于本地查看 trace 和定位问题。

## Progress

- [x] (2026-06-05 16:45+08:00) 阅读 `PLANS.md`，确认 ExecPlan 必须自包含、可执行、包含固定 living document 章节，并且写入 `.md` 文件时不使用外层 fenced code block。
- [x] (2026-06-05 16:50+08:00) 阅读现有架构、技术债和已完成的工具并发 ExecPlan，确认 `services/observability/` 仍是目标模块，TD-007 指向结构化运行事件和 streaming 缺口，工具 executor 已经有并发批次和顺序后处理。
- [x] (2026-06-05 17:00+08:00) 将用户提供的 observability 参考文本转化为适合 OneCode 的本地优先设计：统一 recorder、JSONL sink、span 层级、metadata sanitizer、CLI `/trace`。
- [x] (2026-06-05 19:35+08:00) 实现 `services/observability/` 的事件类型、span recorder、JSONL sink、noop sink 和 metadata sanitizer。
- [x] (2026-06-05 19:50+08:00) 将 trace recorder 装配进 CLI runtime、AgentLoop、RegistryToolExecutor、HookRegistry 和 provider model client 调用路径。
- [x] (2026-06-05 20:00+08:00) 新增 `/trace` CLI 命令，并让 `/status` 显示 trace 文件路径和最近 transition。
- [x] (2026-06-05 20:10+08:00) 增加覆盖 JSONL trace 写入、span 父子关系、metadata 清洗、工具权限等待、hook 和 CLI `/trace` 的测试。
- [x] (2026-06-05 20:20+08:00) 运行目标测试、全量测试和 compileall，并根据结果更新本计划、技术债 TD-007 和 Artifacts。

## Surprises & Discoveries

- Observation: OneCode 的目标架构已经预留 `services/observability/events.py` 和 `services/observability/trace.py`，但目录尚未实现。
  Evidence: `architecture.md` 的目标目录结构列出 `services/observability/`，并说明 `events.py` 定义 trace event，`trace.py` 负责写入 JSONL trace 或提供给 UI 渲染。

- Observation: 当前 CLI 通过 `AgentLoop.run(prompt)` 同步等待最终结果，运行期间只有 `Running...`，没有可订阅事件或 trace 文件。
  Evidence: `ui/cli/app.py` 在普通 prompt 分支中先打印 `renderer.render_running()`，再调用 `runtime.loop.run(line)` 并打印最终 assistant 文本；TD-007 也明确记录 CLI 缺少结构化运行事件和 streaming。

- Observation: 当前 transcript 和未来 trace 必须分离。transcript 保存模型消息，trace 保存运行事实；把 trace 塞进 transcript 会污染模型上下文，也会增加 prompt 泄露风险。
  Evidence: `services/context/transcript.py` 当前只持久化 user、assistant 和 tool_result 消息，并且大 tool result 外置到 `tool-results/`。架构文档将 observability 单独放在 `services/observability/`，而不是 `services/context/`。

- Observation: 工具并发已经落地，trace 设计必须能在 `ThreadPoolExecutor` 下保持父子关系，不应依赖单一线程中的隐式全局状态。
  Evidence: `services/tools/executor.py` 当前使用 `ThreadPoolExecutor` 执行并发安全 handler；如果 trace 只依赖线程局部状态，并发工具 span 可能丢失 `tool_batch` 父 span。

## Decision Log

- Decision: 第一版 observability 只做本地 JSONL trace 和 CLI 展示。
  Rationale: OneCode 是本地 code agent runtime。现阶段最需要的是调试、测试、回放和 UI 展示所需的事实来源。


- Decision: Trace、debug log 和 transcript 必须分离。首版只实现 trace，不实现新的 debug log。
  Rationale: Transcript 是模型对话记录，会被恢复为上下文；trace 是运行事实，不应进入模型消息。Debug log 可以比 trace 更自由地记录诊断文本，但也更容易包含敏感内容，适合作为后续单独计划。


- Decision: 事件入口采用 `TraceRecorder`，调用点只依赖 recorder 协议和 span/event 方法，不直接写文件。
  Rationale: 统一入口能让本地 JSONL、测试内存 sink 和 UI 展示共享同一事实来源，避免 `core/loop.py`、executor、provider 和 CLI 各自手写日志格式。


- Decision: JSONL trace 每条记录都使用 provider-neutral 字段：`record_type`、`timestamp`、`session_id`、`trace_id`、`span_id`、`parent_span_id`、`name`、`attributes`。
  Rationale: 这些字段足够表达普通事件和 span 层级，也能保持 trace 文件稳定可读。Provider-specific 字段不能泄露到 core 层。


- Decision: Metadata 默认做隐私清洗，禁止记录源码内容、prompt 全文、工具输出全文、API key、headers、完整外部路径和任意深层对象。
  Rationale: OneCode 处理代码仓库和 `.env`，observability 默认安全比默认完整更重要。需要更多细节时，应通过显式 debug 模式或未来受控 sink 处理，而不是扩大默认 trace。


- Decision: 并发工具 span 使用显式 `parent_span_id` 传递，而不是只依赖 `contextvars`。
  Rationale: Python 的 `contextvars` 适合同步调用栈和 async task，但 `ThreadPoolExecutor` 不会自动继承所有上下文语义。显式传 parent span 能让并发工具仍挂在正确的 `tool_batch` 下。


## Outcomes & Retrospective

本计划已落地第一版本地结构化 trace。CLI 装配会在 `.onecode/<session_id>/trace.jsonl` 写入当前 session 的 trace；`AgentLoop` 记录 `interaction`、`context_prepare`、`model_call` 和 `transition`；工具执行器记录 `tool_batch`、`tool_preflight`、`permission_wait`、`tool_execution` 和 `tool_result`；hook registry 记录 `hook` span。默认构造均使用 noop recorder，因此非 CLI 测试和第三方装配不强制落盘。

CLI 已新增 `/trace [n]`，`/status` 会显示 trace 文件路径；`/clear` 和 `/resume` 会切换 recorder session。TD-007 已更新为“本地 JSONL trace 和 CLI 查看已落地，但 streaming token、provider recovery UI、debug log 和更丰富的实时 UI 订阅仍未实现”。

## Context and Orientation

OneCode 是一个 Python code agent runtime。它的主循环位于 `core/loop.py`，负责接收用户输入、构建模型上下文、调用模型、执行模型请求的工具并把工具结果回填到消息存储。主循环应保持薄，不直接知道具体工具名、provider 协议、文件格式或 UI 展示细节。

上下文重建位于 `core/context_engine.py`。它从 `services/context/message_store.py` 读取当前会话消息，调用 prompt assembler 和 tool schema provider，返回 `services/context/snapshot.py` 中的 `ContextSnapshot`。这个过程未来会接入 compaction 和 projector；本计划只记录 context prepare 的耗时、消息数量和工具 schema 数量，不改变上下文内容。

模型边界位于 `services/model/client.py` 和 `infrastructure/providers/chat_completions.py`。`ModelClient.send(snapshot)` 当前是同步协议，Chat Completions provider 用 `infrastructure/providers/http.py` 中的 urllib transport 发 HTTP 请求并返回 provider-neutral `LLMResponse`。本计划不改成 streaming 或 async，只在 loop 调用 `model_client.send(snapshot)` 前后记录 `model_call` span，在 `ProviderError` 抛出时记录错误 metadata。

工具运行时位于 `services/tools/executor.py`。`RegistryToolExecutor.execute(tool_calls, state)` 接收模型返回的 `ToolCall` 列表，按工具 descriptor 做 schema 校验、工具校验、input-aware classification、guard、permission、hook、handler 执行、结果预算和成功 side effect。当前 executor 已支持连续 `concurrency_safe=True` 工具 handler 并发执行，但 permission preflight 和结果 finalize 仍按顺序执行。Trace 应记录 `tool_batch`、每个 `tool_call`、permission 等待、handler 执行和工具错误；不要在具体工具实现里手写 trace。

Hook 系统位于 `services/hooks/registry.py` 和 `services/hooks/events.py`。Hook 是生命周期扩展点，当前事件包括 `PreToolUse`、`PostToolUse` 和 `ToolError`。本计划要求 hook registry 记录每次 hook run 的 span，包括事件名称、callback 数量、是否有 blocking error、是否有 updated input，以及 callback 异常数量。Hook payload 里的工具输入不能原样写入 trace。

CLI 位于 `ui/cli/`。`ui/cli/app.py` 装配 runtime；`ui/cli/types.py` 保存 `CliRuntime`；`ui/cli/commands.py` 处理 `/help`、`/tools`、`/status`、`/history`、`/resume`、`/clear`、`/exit` 和 `/quit`；`ui/cli/renderer.py` 输出文本。本计划会给 `CliRuntime` 增加 trace recorder 字段，给 `build_runtime()` 和 resume/clear flow 正确切换 trace session，并新增 `/trace` 命令展示最近 trace 记录。

Transcript 存储位于 `services/context/transcript.py`。它已经实现按 session 写 `.onecode/<session_id>/messages.jsonl`，用 `RLock` 和 `Timer` 缓冲 flush，并在进程退出时 flush。Observability 的 JSONL sink 可以借鉴这个实现，但应写 `.onecode/<session_id>/trace.jsonl`，不要复用 transcript 文件，也不要把 trace record 恢复成模型消息。

本文使用的术语如下。`Trace` 是一次 session 内的结构化运行记录。`Event` 是一个瞬时事实，例如 transition 变为 `tool_use`。`Span` 是有开始和结束时间的一段工作，例如一次模型调用或一次工具执行。`Sink` 是接收 trace record 的后端，例如写 JSONL 文件的 sink 或测试用内存 sink。`Metadata` 或 `attributes` 是附加在 event/span 上的键值对象，例如 token 数、工具名或错误类型。`Sanitizer` 是把 metadata 清洗成安全、有限、不会泄露源码或 secret 的函数。

用户提供的 observability 参考架构包含统一入口、事件 sink、span 层级和 debug/session storage 等设计。OneCode 第一版只吸收适合本地排障的部分：统一入口、span 层级、本地 sink 抽象、metadata 清洗、缓冲 JSONL 写入和 trace/transcript 分离。

## Plan of Work

第一步创建 `services/observability/` 模块。新增 `services/observability/events.py`，定义 trace record 的 dataclass 或 frozen dataclass。建议包含 `TraceRecord`、`TraceSpan` 或更简单的 `TraceRecord` 单一结构。字段必须包括 `record_type`，取值为 `"event"`、`"span_start"` 或 `"span_end"`；`timestamp` 使用 UTC ISO 8601 字符串；`session_id` 来自 `RuntimeState.session_id`；`trace_id` 表示当前 session 或 interaction 的 trace id；`span_id` 和 `parent_span_id` 表示父子关系；`name` 表示事件或 span 名称；`attributes` 是清洗后的 dict。为了让测试稳定，`TraceRecorder` 应允许注入 clock 和 id generator，但生产默认用 `datetime.now(timezone.utc)` 和 `uuid.uuid4()`。

第二步新增 `services/observability/sanitize.py`。实现 `sanitize_attributes(value)` 或 `sanitize_metadata(metadata)`，把 metadata 递归清洗到最多两层、最多二十个 key。允许的标量类型为 `str`、`int`、`float`、`bool` 和 `None`，但字符串必须截断到 240 个字符。Key 名中包含 `key`、`token`、`secret`、`password`、`authorization`、`header`、`env`、`content`、`prompt`、`stdout`、`stderr`、`old_string` 或 `new_string` 时，值必须替换为 `"[redacted]"`，除非 key 是明确安全的计数字段，例如 `input_tokens`、`output_tokens`、`stdout_chars`、`stderr_chars`。路径字段只记录安全摘要：如果路径在 workspace 内，记录相对路径；如果路径在 workspace 外，记录 `"[external_path]"` 和扩展名，不记录完整绝对路径。为了让 sanitizer 能判断 workspace，`TraceRecorder` 构造时应接收 `workspace: Path | None`，并传给 sanitizer。

第三步新增 `services/observability/sinks.py`。定义协议 `TraceSink`，包含 `emit(record: TraceRecord) -> None` 和 `flush() -> None`。实现 `NoopTraceSink` 和 `JsonlTraceSink`。`JsonlTraceSink` 接收 `root_dir`、`session_id`、`flush_interval_seconds` 和可选 clock；写入路径是 `<root_dir>/<session_id>/trace.jsonl`，其中 CLI 会传入 `workspace / ".onecode"` 作为 root。它应像 transcript store 一样用 `RLock`、pending line buffer、`Timer` 和 `atexit.register(self.flush)`。每条 record 用 `json.dumps(..., ensure_ascii=False, separators=(",", ":"))` 写一行。`switch_session(session_id)` 必须先 flush 再切换 session，供 `/clear` 和 `/resume` 使用。

第四步新增 `services/observability/trace.py`。定义 `TraceRecorder`，它持有 `session_id`、`trace_id`、workspace、sink 和一个 `contextvars.ContextVar[str | None]` 保存当前 span id。它提供三个核心方法。`event(name, attributes=None, parent_span_id=None)` 写一条 `"event"` record。`start_span(name, attributes=None, parent_span_id=None)` 写 `"span_start"` 并返回 `TraceSpan`。`span(name, attributes=None, parent_span_id=None)` 返回 context manager，用 `with` 包裹一段同步代码，进入时 start，退出时 end。`TraceSpan.end(attributes=None, error=None)` 写 `"span_end"`，计算 `duration_ms`，并在异常时记录 `error_type` 和安全错误消息。`TraceRecorder` 还应有 `flush()`、`switch_session(session_id)`、`trace_path` 属性和 `recent_records(limit)` helper。`recent_records()` 可以从 sink 文件读取最后若干行并解析 JSON；如果 sink 是 Noop，则返回空列表。

第五步在 `services/observability/__init__.py` 导出公共类型。外部模块应只 import `TraceRecorder`、`TraceSink`、`NoopTraceSink`、`JsonlTraceSink` 和可能的 helper，不应依赖内部私有类。

第六步装配 CLI runtime。编辑 `ui/cli/types.py`，给 `CliRuntime` 增加 `trace_recorder: TraceRecorder` 字段。`with_session()` 中创建新的 `AgentLoop` 时要继续传入同一个 recorder，并在 session 切换时调用 `trace_recorder.switch_session(state.session_id)`。编辑 `ui/cli/app.py` 的 `build_runtime()`，创建 `JsonlTraceSink(workspace / ".onecode", state.session_id)` 和 `TraceRecorder(session_id=state.session_id, workspace=workspace, sink=sink)`，再把 recorder 注入 `AgentLoop`、`RegistryToolExecutor` 和 `CliRuntime`。如果未来非 CLI 装配不想写文件，默认构造可以使用 `NoopTraceSink`，所以生产以外测试不会被迫落盘。

第七步给 `core/loop.py` 埋点。`AgentLoop.__init__()` 增加可选参数 `trace_recorder: TraceRecorder | None = None`，内部默认 `TraceRecorder.noop(state.session_id)` 或 `NoopTraceRecorder`。`run(prompt)` 用 `with recorder.span("interaction", {"user_prompt_length": len(prompt)})` 包住 append user 和 `run_loop()`。`run_loop()` 每轮开始记录 turn number。构建 context 时用 `context_prepare` span 包住 `context_engine.build_for_model(state)`，span 结束 attributes 包含 `message_count`、`tool_schema_count` 和 `has_system_prompt`，不包含 prompt 文本或消息内容。模型调用用 `model_call` span 包住 `model_client.send(snapshot)`，结束时记录 `tool_call_count`、`stop_reason`、usage token 计数和 `output_interrupted`。如果抛出 `ProviderError`，记录 `provider_id`、`error_type`、`status_code`、`retryable`，然后继续按现有行为 re-raise，不在本计划中实现恢复。每次 `state.set_transition(...)` 后调用 `recorder.event("transition", {"transition": transition.value, "turn_count": state.turn_count})`。

第八步给 `services/tools/executor.py` 埋点。`RegistryToolExecutor.__init__()` 增加可选 `trace_recorder` 参数，默认 noop。`execute()` 对整个 tool call 列表创建 `tool_batch` span，attributes 包含 `tool_call_count` 和 `concurrency_candidate_count`。`_preflight_one()` 创建 `tool_preflight` span，attributes 包含 `tool_name`、`tool_call_id`、`target_count`、`read_only`、`modifies_filesystem`、`concurrency_safe`、permission decision action 和 guard policy action 摘要。不要记录原始 tool input。`_evaluate_permission()` 在调用 `permission_prompter.request_permission(request)` 之前创建 `permission_wait` span，结束时记录用户 decision、scope 和是否 interrupted。`_run_handler()` 创建 `tool_execution` span，parent 使用当前 tool call span 或显式 parent id；并发 worker 中不得依赖隐式 context。`_finalize_outcome()` 记录 `tool_result` event，attributes 包含 `tool_name`、`tool_call_id`、`is_error`、`error`、`content_chars`、`result_truncated` 和 `duration_ms`，不要记录 `result.content`。

第九步给 `services/hooks/registry.py` 埋点。`HookRegistry.__init__()` 增加可选 `trace_recorder` 参数或增加 `set_trace_recorder()`，默认 noop，避免所有测试都需要改构造。`run(event, payload)` 用 `hook` span 包住 callback 执行，attributes 包含 `hook_event`、`callback_count`、`blocking`、`updated_input` 和 `hook_error_count`。由于 hook registry 不应 import 具体 tool modules，它只能从 payload 中读取 `descriptor.name` 或 `tool_call.name` 作为安全摘要。

第十步给 provider 调用路径补充信息。首版主要在 `core/loop.py` 的 `model_call` span 记录 provider-neutral 信息即可。如果需要 provider id 和 model，`OpenAICompatibleChatCompletionsClient` 有 `config.provider_id` 和 `config.model`，可以在 `model_call` span 中用 `getattr(self.model_client, "config", None)` 读取安全字段；不要让 core import 具体 provider 类型。不要记录 URL、headers、API key 或 payload messages。

第十一步新增 CLI `/trace`。编辑 `ui/cli/commands.py`，识别 `/trace`，默认显示最近 20 条 trace record；如果用户传数字，例如 `/trace 50`，显示最近 50 条。编辑 `ui/cli/renderer.py` 增加 `render_trace(records)`，每条记录显示 timestamp、record_type、name、duration_ms、tool_name、transition、error 等安全摘要。编辑 `/help` 输出，加入 `/trace [count]`。编辑 `render_status(runtime)`，显示 trace 文件路径，路径来自 `runtime.trace_recorder.trace_path`，Noop sink 时显示 `disabled`。

第十二步补充测试。新增 `tests/test_observability_trace.py`，覆盖 `TraceRecorder` 写 event/span、JSONL sink flush、span 父子关系、异常 span 记录 error、metadata sanitizer redaction 和 path sanitizer。更新 `tests/test_loop.py` 或新增测试，使用 fake model client 和 in-memory sink，运行一次 loop，断言出现 `interaction`、`context_prepare`、`model_call` 和 `transition`。更新 `tests/test_tool_registry_and_executor.py`，用 fake descriptor 和 fake prompter，断言出现 `tool_batch`、`tool_preflight`、`permission_wait`、`tool_execution` 和 `tool_result`，并且 result content 没有进入 attributes。更新 `tests/test_hooks.py`，断言 hook span 记录 callback 数量和 blocking 状态。更新 `tests/test_cli_commands.py`，断言 `/trace` 会调用 renderer 并输出 recent trace 摘要。

第十三步更新技术债。实现和测试通过后，编辑 `docs/tech-debt/tech-debt-tracker.md` 的 TD-007，说明本地 JSONL trace 和 CLI `/trace` 已落地，但 streaming token、provider recovery UI 和 debug log 仍未实现。不要关闭 TD-007，除非同时完成 streaming 和 UI 订阅。

## Concrete Steps

从仓库根目录 `D:\study\OneCode` 开始。先运行当前相关测试确认基线：

    uv run python -m pytest tests\test_loop.py tests\test_tool_registry_and_executor.py tests\test_hooks.py tests\test_cli_commands.py -q

预期当前基线应通过。如果失败，先记录失败到本计划的 `Surprises & Discoveries`，判断是否与 observability 相关。不要在未理解失败原因前修改实现。

创建目录和文件：

    services\observability\__init__.py
    services\observability\events.py
    services\observability\sanitize.py
    services\observability\sinks.py
    services\observability\trace.py

在 `events.py` 中定义 record 数据结构。建议接口如下：

    @dataclass(frozen=True)
    class TraceRecord:
        record_type: Literal["event", "span_start", "span_end"]
        timestamp: str
        session_id: str
        trace_id: str
        name: str
        span_id: str | None = None
        parent_span_id: str | None = None
        attributes: dict[str, Any] = field(default_factory=dict)

    def record_to_json_dict(record: TraceRecord) -> dict[str, Any]:
        ...

字段命名要稳定，测试应按 JSON key 断言。不要把 Python 对象直接 `str()` 后写入 JSON。

在 `sanitize.py` 中实现 metadata 清洗。至少提供：

    def sanitize_attributes(
        attributes: Mapping[str, Any] | None,
        *,
        workspace: Path | None = None,
    ) -> dict[str, Any]:
        ...

测试必须证明 `{"api_key": "secret"}`、`{"prompt": "full prompt"}`、`{"content": "source"}`、`{"old_string": "source"}` 会变成 `"[redacted]"`，并证明长字符串会截断。

在 `sinks.py` 中定义：

    class TraceSink(Protocol):
        def emit(self, record: TraceRecord) -> None: ...
        def flush(self) -> None: ...

    class NoopTraceSink:
        ...

    class JsonlTraceSink:
        def __init__(
            self,
            root_dir: Path,
            session_id: str,
            *,
            flush_interval_seconds: float = 1.0,
        ) -> None: ...
        @property
        def trace_path(self) -> Path: ...
        def switch_session(self, session_id: str) -> None: ...

`JsonlTraceSink` 可以参考 `services/context/transcript.py` 的 flush 模式，但不要 import transcript store 或把 trace 写进 messages.jsonl。

在 `trace.py` 中定义：

    class TraceRecorder:
        def __init__(self, *, session_id: str, workspace: Path | None = None, sink: TraceSink | None = None) -> None: ...
        @classmethod
        def noop(cls, session_id: str = "") -> "TraceRecorder": ...
        def event(self, name: str, attributes: Mapping[str, Any] | None = None, *, parent_span_id: str | None = None) -> None: ...
        def start_span(self, name: str, attributes: Mapping[str, Any] | None = None, *, parent_span_id: str | None = None) -> TraceSpan: ...
        def span(self, name: str, attributes: Mapping[str, Any] | None = None, *, parent_span_id: str | None = None): ...
        def flush(self) -> None: ...
        def switch_session(self, session_id: str) -> None: ...
        def recent_records(self, limit: int = 20) -> list[dict[str, Any]]: ...

    class TraceSpan:
        @property
        def span_id(self) -> str: ...
        def end(self, attributes: Mapping[str, Any] | None = None, *, error: BaseException | None = None) -> None: ...

`TraceSpan` 必须支持 context manager 协议。`with recorder.span("model_call"):` 正常退出时写 `span_end`；异常退出时写带 error metadata 的 `span_end`，然后重新抛出异常。

更新 `core/loop.py`。构造函数新增 `trace_recorder` 可选参数；没有传入时使用 noop。包裹 interaction、context prepare 和 model call，并记录 transition event。保持 `AgentLoop.run(prompt) -> str` 和 `run_loop() -> str` 返回值不变。不要把 `snapshot.system_prompt`、`snapshot.messages` 或 assistant final text 写入 trace。

更新 `services/tools/executor.py`。构造函数新增 `trace_recorder` 可选参数；没有传入时使用 noop。围绕 `execute()`、`_preflight_one()`、permission prompt、`_run_handler()` 和 `_finalize_outcome()` 记录 span/event。由于 `_run_handler()` 可能在线程池 worker 中执行，进入 worker 前必须把 parent span id 作为显式值保存在 `_ReadyToolCall` 或 `_HandlerOutcome` 可访问的位置，或者在提交任务时把 parent span id 作为参数传入。不要依赖 worker 能自动看到主线程当前 span。

更新 `services/hooks/registry.py`。保持现有 `HookRegistry()` 无参构造可用。新增可选 recorder 后，测试中没有 recorder 时不落盘。`run()` 的返回值和异常吞掉策略不能改变。

更新 `ui/cli/app.py` 和 `ui/cli/types.py`。`build_runtime()` 创建 `JsonlTraceSink` 和 `TraceRecorder`，传给 loop、executor、hooks 和 runtime。注意当前 `RegistryToolExecutor` 构造时如果没有显式 hooks，会创建自己的 `HookRegistry()`；为了 hook trace 生效，可以在 CLI 装配中显式创建 `HookRegistry(trace_recorder=recorder)` 并传入 executor。`CliRuntime.with_session()` 和 `/clear`、`/resume` 后必须让 recorder 切到新的 session id，否则 trace 会写到旧目录。

更新 `ui/cli/commands.py` 和 `ui/cli/renderer.py`。新增 `/trace [count]`，并在 `/help` 里说明它显示当前 session 最近 trace 事件。`/trace` 解析 count 的规则可以复用 `/history` 的正整数逻辑；非法 count 输出 `renderer.render_error("trace count must be an integer.")`。`render_trace(records)` 应在没有记录时返回 `"No trace records."`。

添加测试后运行目标测试：

    uv run python -m pytest tests\test_observability_trace.py tests\test_loop.py tests\test_tool_registry_and_executor.py tests\test_hooks.py tests\test_cli_commands.py -q

然后运行全量测试：

    uv run python -m pytest tests -q

最后运行 compile check：

    uv run python -m compileall core services infrastructure tools ui

如果命令输出有失败，把失败摘要记录到 `Surprises & Discoveries`，修复后再次运行。完成后把关键通过输出摘录到 `Artifacts and Notes`。

## Validation and Acceptance

第一类验收是低层 trace 行为。运行 `uv run python -m pytest tests\test_observability_trace.py -q` 应通过。测试应证明创建 recorder 后调用 `event("transition", {"transition": "completed"})` 会写出一条 JSONL；使用 `with recorder.span("model_call"):` 会写出一条 `span_start` 和一条 `span_end`；`span_end.attributes.duration_ms` 是非负数字；嵌套 span 的 `parent_span_id` 等于父 span 的 `span_id`；异常 span 会记录 `error_type`，但异常继续抛出。

第二类验收是隐私清洗。测试应构造包含 `api_key`、`Authorization`、`prompt`、`content`、`old_string`、`new_string`、`stdout`、`stderr`、深层 dict、超长字符串和 workspace 外路径的 metadata。写出的 JSONL 中不得包含 secret 原文、源码片段、完整 prompt、完整 stdout/stderr 或外部绝对路径。允许记录 `stdout_chars`、`stderr_chars`、`input_tokens`、`output_tokens` 这类计数。

第三类验收是 runtime 行为。用 fake model client 运行 `AgentLoop.run("hello")` 后，内存 sink 或 JSONL sink 中应出现 `interaction`、`context_prepare`、`model_call` 和 `transition`。如果 fake model 返回 tool call，executor trace 中应出现 `tool_batch`、`tool_preflight`、`tool_execution` 和 `tool_result`。如果 permission prompter 被调用，应出现 `permission_wait` span，并记录用户允许或拒绝。工具结果内容不能出现在 trace attributes。

第四类验收是 CLI 行为。运行 CLI 或命令测试后，当前 session 目录应存在 `.onecode/<session_id>/trace.jsonl`。调用 `/trace` 应显示最近 trace 摘要，不应显示 JSON 原文中的敏感字段。调用 `/status` 应显示 trace 文件路径或 disabled。`/clear` 后新 session 的 trace 应写入新目录；`/resume <session>` 后 trace 应跟随恢复后的 session。

第五类验收是兼容性。现有 `AgentLoop.run()`、`RegistryToolExecutor.execute()`、`HookRegistry.run()`、CLI slash commands 和 provider tests 不应因为没有显式 recorder 而失败。Noop recorder 必须保证非 CLI 测试和应用装配可以不写任何文件。

全量验收命令如下，均应成功：

    uv run python -m pytest tests -q
    uv run python -m compileall core services infrastructure tools ui

## Idempotence and Recovery

本计划是 additive change，创建新模块并给现有构造函数增加可选参数。可以反复运行测试和 compile check。`JsonlTraceSink.flush()` 只追加已经缓冲的 record；重复运行测试会写入 pytest 临时目录，不应污染真实项目 `.onecode`，除非手动启动 CLI。

如果 trace 文件写入失败，sink 不应让 agent 主流程崩溃。首版可以在 `JsonlTraceSink.emit()` 中把序列化或 enqueue 异常吞掉并计入内部 `dropped_count`，但不要 print；测试应覆盖一个 failing sink 不影响 `AgentLoop.run()`。如果需要暴露 dropped count，可通过 `/status` 后续展示。

如果发现某个 trace 调用点会记录敏感内容，优先修 sanitizer 和测试，而不是删除整个 span。Observability 的目标是保留安全摘要，不是完全沉默。

如果并发工具 trace 父子关系不稳定，优先改为显式传递 parent span id。不要为了方便把工具并发调度退回串行，也不要依赖全局 mutable current span。

如果新增 `/trace` 命令在没有 trace 文件时失败，应改为显示 `"No trace records."`。用户不应因为 trace 文件缺失而无法使用 CLI。

## Artifacts and Notes

实现输出：

    uv run python -m pytest tests\test_observability_trace.py tests\test_loop.py tests\test_tool_registry_and_executor.py tests\test_hooks.py tests\test_cli_commands.py -q
    40 passed in 1.38s

    uv run python -m compileall core services infrastructure tools ui
    succeeded

    uv run python -m pytest tests -q
    141 passed in 2.48s

新增和更新的重点测试包括：

    tests/test_observability_trace.py::test_jsonl_sink_writes_event_and_span_records
    tests/test_observability_trace.py::test_sanitizer_redacts_sensitive_metadata_and_paths
    tests/test_loop.py::test_loop_records_interaction_model_and_transition_trace
    tests/test_tool_registry_and_executor.py::test_executor_records_tool_permission_and_result_trace
    tests/test_hooks.py::test_hook_registry_records_hook_trace
    tests/test_cli_commands.py::test_trace_command_renders_recent_trace_records

安全 trace 摘要示例：

    {"record_type":"span_start","name":"interaction","attributes":{"user_prompt_length":5}}
    {"record_type":"span_end","name":"model_call","attributes":{"duration_ms":1.2,"tool_call_count":0,"input_tokens":3,"output_tokens":5}}
    {"record_type":"event","name":"transition","attributes":{"transition":"completed","turn_count":1}}

以下为计划初始占位示例，保留作格式参考：

    tests/test_observability_trace.py::test_jsonl_sink_writes_event_and_span_records PASSED
    tests/test_observability_trace.py::test_sanitizer_redacts_prompt_content_and_secrets PASSED
    tests/test_loop.py::test_loop_records_interaction_model_and_transition_trace PASSED
    tests/test_tool_registry_and_executor.py::test_executor_records_tool_permission_and_result_trace PASSED
    uv run python -m pytest tests -q
    <N> passed in <seconds>s

完成后也应记录一条真实或测试生成的 trace JSONL 摘要，格式类似：

    {"record_type":"span_start","name":"interaction","session_id":"...","span_id":"...","attributes":{"user_prompt_length":12}}
    {"record_type":"span_end","name":"model_call","session_id":"...","span_id":"...","attributes":{"duration_ms":123,"tool_call_count":1,"input_tokens":42}}
    {"record_type":"event","name":"transition","session_id":"...","attributes":{"transition":"tool_use","turn_count":1}}

这些示例只能包含安全摘要，不能包含真实源码、prompt 全文、tool output 全文或 secret。

## Interfaces and Dependencies

本计划只使用 Python 标准库：`dataclasses`、`datetime`、`json`、`pathlib`、`threading`、`contextvars`、`uuid`、`atexit` 和 `typing`。不要新增第三方依赖。

新增公共接口位于 `services/observability/`：

    class TraceSink(Protocol):
        def emit(self, record: TraceRecord) -> None: ...
        def flush(self) -> None: ...

    class JsonlTraceSink:
        @property
        def trace_path(self) -> Path: ...
        def emit(self, record: TraceRecord) -> None: ...
        def flush(self) -> None: ...
        def switch_session(self, session_id: str) -> None: ...

    class TraceRecorder:
        @classmethod
        def noop(cls, session_id: str = "") -> "TraceRecorder": ...
        def event(self, name: str, attributes: Mapping[str, Any] | None = None, *, parent_span_id: str | None = None) -> None: ...
        def start_span(self, name: str, attributes: Mapping[str, Any] | None = None, *, parent_span_id: str | None = None) -> TraceSpan: ...
        def span(self, name: str, attributes: Mapping[str, Any] | None = None, *, parent_span_id: str | None = None): ...
        def flush(self) -> None: ...
        def switch_session(self, session_id: str) -> None: ...
        def recent_records(self, limit: int = 20) -> list[dict[str, Any]]: ...

Existing constructors gain optional parameters only:

    AgentLoop(..., trace_recorder: TraceRecorder | None = None)
    RegistryToolExecutor(..., trace_recorder: TraceRecorder | None = None)
    HookRegistry(trace_recorder: TraceRecorder | None = None)

These additions must not break existing tests or third-party callers. Defaults must use a noop recorder.

Trace event names should be stable and snake_case:

    interaction
    context_prepare
    model_call
    tool_batch
    tool_preflight
    permission_wait
    tool_execution
    tool_result
    hook
    transition

Allowed span/event attributes include counts, durations, booleans, enum-like strings and safe names:

    duration_ms
    turn_count
    message_count
    tool_schema_count
    tool_call_count
    tool_name
    tool_call_id
    read_only
    modifies_filesystem
    concurrency_safe
    permission_action
    guard_actions
    transition
    provider_id
    model
    status_code
    error_type
    retryable
    input_tokens
    output_tokens
    cache_read_input_tokens
    output_interrupted
    result_truncated
    content_chars

Forbidden attributes or values include API keys, headers, environment values, prompt text, message content, assistant final text, tool result content, stdout/stderr text, edit old/new strings, and full external absolute paths. Sanitizer tests must enforce this contract.

Future work intentionally left out of this plan includes debug log files, `/debug` mode, streaming token events, provider retry UI and compaction trace details beyond placeholder span names.

## Change Note

2026-06-05 / Codex: 新增本 ExecPlan，记录 OneCode 第一版本地 observability trace 的实现设计。计划吸收用户提供参考架构中的统一入口、span 层级、本地 sink、metadata 清洗和 trace/transcript 分离思想，范围限定为本地查看 trace 和定位问题。

2026-06-05 / Codex: 根据用户反馈收窄计划表述。计划只描述本地 JSONL trace、CLI 查看和问题定位所需能力。
