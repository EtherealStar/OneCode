# Execution

所有命令从仓库根目录 `/mnt/d/study/OneCode` 运行。开始前阅读 [plan.md](./plan.md)、[decisions.md](./decisions.md)、`architecture.md` 和关联设计文档，记录 `git status --short`。每批结束把实际命令和结果写入 [progress.md](./progress.md)。

## Milestone 1: 锁定 SDK 与可重复的请求契约

### Batch 1: 固定依赖与验证 SDK 接口

在 `pyproject.toml` 显式添加 `openai==3.18.0`，运行 `uv lock` 和 `uv sync --dev`，将解析结果写入 `uv.lock`。调研报告引用的 `main` 分支不是版本契约；核对安装的 3.18.0 中 `AsyncOpenAI`/`OpenAI`、`chat.completions.create(stream=True)`、`models.list()`、`max_retries`、`timeout`、`default_headers`、`extra_body`、`AsyncStream.close()` 的接口。若确有 API 差异，先在 [decisions.md](./decisions.md) 记录实测及所选版本后改计划和依赖。

用 SDK 支持的 `httpx.MockTransport` 注入 HTTP client，添加独立测试，观察 `base_url` 拼接、自定义 header、Bearer key、60 秒 timeout、标准/额外参数、SSE 完成、错误对象与 models 列表。验证 SDK `max_retries=0` 时一个可重试状态只产生一次 HTTP 请求。不要调用真实服务。

运行 `uv run python -m pytest tests/test_openai_compatible_provider.py tests/test_openai_compatible_provider_streaming.py -q`（迁移初期仍包括旧用例），并运行新 SDK 契约用例。完成标准：锁定版本与 mock 请求证据记录在 progress，SDK API 形状无需推测。

### Batch 2: 明确请求参数与错误契约

在适配器测试中固定现有 `ONECODE_DEFAULT_PARAMS` 与 `ContextSnapshot.usage_hints["request_overrides"]` 的覆盖顺序：内部决定 `model`、`messages`、`tools`、`stream` 和输出上限；配置中的标准可选 Chat 参数作为 SDK 具名参数，协议允许而 SDK 未暴露的额外 JSON 字段经 `extra_body` 传递；不能让配置覆盖内部保留字段。`ONECODE_EXTRA_HEADERS` 继续生效，但认证由 SDK `api_key` 负责。可选 `stream_options.include_usage` 只在请求明确启用时发送，usage chunk 缺失也应正常完成。若现有配置表达能力与 SDK 参数签名冲突，先以测试显示差异，再在 decisions 中记录取舍，不静默丢字段。

完成标准：测试能断言请求体、headers、URL 和事件所需字段；无 `attachment` role 泄漏。

## Milestone 2: 模型流通过 SDK，运行时事件保持稳定

### Batch 1: 替换模型客户端内部调用

在 `infrastructure/providers/factory.py` 创建并注入一个 `AsyncOpenAI`，配置来自 `ResolvedProviderConfig`，显式设 `timeout=config.timeout_seconds`、`max_retries=0` 和 `base_url`。无密钥的本地 OpenAI 兼容端可向 SDK 传仅用于满足其构造要求的非秘密占位值；OneCode 不应将 SDK 环境变量当配置来源。`infrastructure/providers/chat_completions.py` 保持 `ModelClient.stream(snapshot)` 接口，通过 SDK `chat.completions.create(stream=True)` 消费 typed chunks。使用 `AsyncStream.close()` 或该锁定版本对应的异步关闭方法，确保正常、异常、取消和调用方提前 `aclose()` 均释放流；`CancelledError` 原样传播。

继续由 OneCode 投影 system/user/assistant/tool 消息，并聚合分片工具调用，因为 `ModelStreamEvent.tool_call_delta` 和最终完整 `ToolCall` 是 OneCode 契约。`message_completed.metadata["tool_calls"]`、`assistant_message` 的 wire 形状、`stop_reason`、`output_interrupted`、`ModelUsage` 映射保持可用于下一轮。只消费 SDK 提供的类型化字段；不要另造 SSE 行解析。对 malformed 响应在边界生成 `ProviderError(invalid_response)`，不要把 SDK/Pydantic/JSON 异常泄漏到 loop。

完成标准：`uv run python -m pytest tests/test_openai_compatible_provider_streaming.py tests/test_openai_compatible_provider.py tests/test_runtime_integration.py -q` 通过；测试使用 SDK 的 HTTP mock，而不是旧 `AsyncHttpTransport` fake。

### Batch 2: 错误、重试与跨轮行为

在 infrastructure 边界把 SDK `APIStatusError`、`APIConnectionError`、`APITimeoutError` 和流消费阶段解码错误映射到现有 `ProviderError`；保留 provider id、状态码、上下文超限识别、可重试规则与安全日志。`Retry-After` 若是有效非负秒数可填入 `retry_after_seconds`；无效值忽略。不要把错误正文中的凭证输出到 trace。SDK 请求级重试关闭，`services/model/retry.py` 保留指数退避、attempt trace、部分输出可见的现行语义。

增加本地协议测试覆盖：交错的多工具调用分片、坏 JSON 参数、usage 有/无、`length`、调用中断/提前关闭、连接前失败与中途失败、429/5xx/401/413/上下文超限、retry-after、工具执行后下一轮请求含正确 `tool_call_id`。运行 `uv run python -m pytest tests/test_model_retry.py tests/test_loop.py tests/test_runtime_integration.py tests/test_import_boundaries.py -q`。完成标准：失败时无 SDK 异常越过 provider 边界；重试请求次数与 OneCode 策略一致；stream 资源在取消后关闭。

## Milestone 3: 标准模型 API 与客户端生命周期收口

### Batch 1: 模型发现和连接探测

在 `infrastructure/providers/model_catalog.py` 保留 `ProviderModel`、候选 URL 的选择和模型列表排序。标准 `/models` 候选改由同步 `OpenAI.models.list()` 或合适的 SDK client 调用；`/connect` 的标准 Chat Completions 连通性测试改用 SDK `chat.completions.create(stream=False)`。探测每个候选时配置对应 `base_url`，不自行拼标准 API 请求。Ollama 原生 `/api/tags` 模型发现和 `/api/chat` 连通性探测继续走隔离的通用 HTTP 请求函数；不要把它们伪装成 SDK resource。模型探测失败的 fallback 顺序应保持现有行为，返回 `ProviderModel` 和用户可读错误。

运行 `uv run python -m pytest tests/test_cli_connect.py tests/test_openai_compatible_provider.py -q`。完成标准：HTTP mock 显示标准 `/models` 与 `/chat/completions` 由 SDK 发出；Ollama 专有模型发现用例仍通过；不要求外部供应商实测。

### Batch 2: 所有权、关闭和旧传输删除

在 `application/runtime.py` 明确 model client 由应用拥有，session rebind 不复制所有权，子 agent/记忆选择器共享该 client。`application/session.py` 在关闭 worker、子任务和正在消费的流后关闭当前 SDK client；配置热重载先构建新 client，成功安装后再关闭旧 client，失败时关闭新 client 并保留旧配置。确保重复 close 安全。`factory.py` 的测试注入 seam 改成 SDK client/http client，去掉 `async_transport`。删除 `http.py` 中无调用者的 `HttpxAsyncHttpTransport`、`AsyncHttpTransport`、`parse_sse_json_line`；保留被非标准 endpoint 使用的通用 HTTP 与共享错误归一化函数。根据实际直接导入者决定是否保留 `httpx` 显式依赖（当前 `services/mcp/manager.py` 直接导入，必须保留）。

运行 `uv run python -m pytest tests/test_session_controller.py tests/test_session_interrupt_cleanup.py tests/test_cli_connect.py tests/test_import_boundaries.py -q`。完成标准：关闭/热重载测试确认每个 SDK client 恰好关闭，运行中请求不被提前关闭，旧异步传输及 SSE parser 没有生产调用者。

## Milestone 4: 跨模块验收与文档对齐

### Batch 1: 端到端本地协议演练

以 `httpx.MockTransport` 或本地 HTTP fixture 模拟两次标准 Chat Completions 回复：第一次流式发出工具调用，第二次读到工具结果后发出文本。通过 `application`/`AgentLoop` 的公共接口驱动，验证工具执行器确实运行、transcript 有配对的 assistant/tool_result、最终 stream 向 UI 逐段输出。另验证 429 一次后成功的请求次数和 trace 中一次 `model_retry`。运行相关集成测试和 `uv run python -m pytest tests -q`；预期全部通过。

### Batch 2: 文档和清理

对照实际实现核对先行写好的 `docs/design-docs/model-provider-architecture.md`、`architecture.md` 和 `docs/design-docs/core-runtime-architecture.md`，将“尚未实现”的状态更新为事实，并修正实现期间产生的任何契约偏差；不要恢复旧的缓冲式重试描述。运行 `uv run python -m pytest tests/test_import_boundaries.py -q`、`uv run python -m compileall core services infrastructure application` 和完整 suite。检查 `rg -n 'HttpxAsyncHttpTransport|parse_sse_json_line|AsyncHttpTransport' infrastructure application core services` 无生产引用，并用 `git diff --check` 检查新增改动。全部验收达成后填充 progress 的 Outcomes，并将本计划目录整体移到 `docs/exec-plans/completed/`。

## Idempotence And Recovery

`uv lock`/`uv sync --dev` 可重跑；测试仅使用临时 `.env` 和 mock HTTP。每批修改之前先看 `git diff -- <目标文件>`，保留现有用户改动。若 SDK 锁定版的 API 与预期不同，隔离失败测试、记录实际行为，先改适配方案再继续，不以手写 HTTP/SSE 长期绕过 SDK。热重载和关闭若测试失败，保持旧 client 活着并恢复可用运行时，再重试该批。不要以真实 API 或某家供应商的特例修补本地契约。
