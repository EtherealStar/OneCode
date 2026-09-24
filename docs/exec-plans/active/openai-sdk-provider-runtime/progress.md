# Progress

实施者在每个停止点更新此文件，并遵循仓库根目录 [PLANS.md](../../../../PLANS.md)。

## Current State

- Status: Milestone 1, 2 and 3 complete; Milestone 4 not started.
- Current milestone: Milestone 3, 标准模型 API 与客户端生命周期收口（已完成）。
- Last updated: 2026-09-24 Asia/Shanghai.
- Working tree: 计划创建前已有大量已修改文件；不可将这些改动视为本计划成果，也不可重置。

## Milestones And Batches

- [x] Milestone 1: 锁定 SDK 与可重复的请求契约。
  - [x] Batch 1: 固定依赖与验证 SDK 接口。
  - [x] Batch 2: 明确请求参数与错误契约。
- [x] Milestone 2: 模型流通过 SDK，运行时事件保持稳定。
  - [x] Batch 1: 替换模型客户端内部调用。
  - [x] Batch 2: 错误、重试与跨轮行为。
- [x] Milestone 3: 标准模型 API 与客户端生命周期收口。
  - [x] Batch 1: 模型发现和连接探测。
  - [x] Batch 2: 所有权、关闭和旧传输删除。
- [ ] Milestone 4: 跨模块验收与文档对齐。
  - [ ] Batch 1: 端到端本地协议演练。
  - [ ] Batch 2: 文档和清理。

## Surprises And Discoveries

- Observation: `openai==3.18.0` 的默认 HTTP 客户端来自新依赖 `httpx2`（`httpx2==2.13.1`、`httpcore2`、`httpx2-jsfetch`），而不是项目的 `httpx`；但 SDK 通过 `_httpx2.py` 同时接受旧 `httpx` client，`httpx.MockTransport` 注入可用。已记录到 [decisions.md](./decisions.md)。
  Evidence: `uv lock`/`uv sync --dev` 解析出 `httpx2==2.13.1`；契约测试用 `httpx.AsyncClient(transport=httpx.MockTransport(...))` 注入 SDK 并成功观察请求。
- Observation: `AsyncStream.close()` 只关闭底层 response，不保证后续 `__anext__` 抛 `StopAsyncIteration`（MockTransport 下仍可能返回已缓冲 chunk）。关闭语义测试只断言 close 幂等与 client 关闭安全。
  Evidence: `openai/_streaming.py` 中 `close()` 调 `response.aclose()`；`test_sdk_stream_close_is_safe_after_partial_consumption`。
- Observation: `services/model/retry.py` 立即转发每个事件；`docs/design-docs/model-provider-architecture.md` 仍记载先缓冲所有事件。
  Evidence: `ModelRetryRunner.stream()` 中的 `async for event in operation(): yield event`。
- Observation: `/connect` 的标准模型列表和模型连通性探测还用 `UrllibHttpTransport`；只替换 async 模型流无法达成“能由 SDK 接管的由 SDK 接管”。
  Evidence: `infrastructure/providers/model_catalog.py` 中的 `fetch_models_for_connect()` 与 `test_model_connection()`。
- Observation: `application/runtime.py::with_model_config()` 会换掉 client，但当前没有关闭旧 client 的流程；`services/mcp/manager.py` 直接依赖 `httpx`，删除模型专用 httpx 传输后也不能移除项目的显式 httpx 依赖。
- Observation: 目标文档已先行修改；当前代码仍使用自建 HTTP/SSE，实施完成时需要再次核对文档状态与实际接口。
  Evidence: `docs/design-docs/model-provider-architecture.md`、`architecture.md`、`docs/design-docs/core-runtime-architecture.md`。

### Milestone 2

- Observation: SDK typed chunk 的 `delta.tool_calls` 是 `ChoiceDeltaToolCall` 对象（属性 `index`/`id`/`function.name`/`function.arguments`），`usage` 是 `CompletionUsage`（`prompt_tokens_details.cached_tokens`）。聚合逻辑改为读取属性，不再解析手写 dict。
- Observation: `create(stream=True)` 在错误发生在建流之前时若返回 5xx，抛 `APIStatusError` 子类，SDK 不重试（`max_retries=0`）；调用方可直接映射。中途断流（`httpx.ReadError`）被 SDK 包装为 `APIConnectionError`，已产出的 `content_delta` 保持可见。
- Observation: `AsyncOpenAI.close()` 幂等；adapter 通过 `aclose()` 关闭其持有的 client，供 M3 接线。测试注入的 SDK client 由测试自行关闭，不调用 `aclose()`。

### Milestone 3

- Observation: SDK 的 `Model` 对象允许额外字段（`model_extra`），非标准 `display_name` 可经 `getattr`/`model_dump()` 读取，`owned_by` 是标准字段。
- Observation: `fetch_models_for_connect` 的候选改为 base URL（`{base_url}/v1` 与 `{base_url}`），由 SDK 追加 `/models`；探测多个候选时只在自建 `http_client` 时关闭 client，注入的共享 `http_client` 不关闭。
- Observation: `close_model_client` 统一处理适配器的异步 `aclose()` 与原始 SDK client 的异步 `close()`，重复关闭安全。

## Validation Evidence

### Milestone 1

- 依赖：`pyproject.toml` 显式加入 `openai==3.18.0`；`uv lock` 与 `uv sync --dev` 成功，新增 `openai 3.18.0`、`httpx2 2.13.1`、`httpcore2 2.13.1`、`httpx2-jsfetch 1.0`、`jiter 0.17.0`、`sniffio 1.3.1`、`truststore 0.10.4`，结果写入 `uv.lock`。
- 新契约用例：`uv run python -m pytest tests/test_openai_sdk_provider_contract.py -q` → 15 passed。
  - 覆盖：base_url 拼接、Bearer `api_key`、`default_headers`、`timeout`、`max_retries=0` 单请求、typed stream 的文本/tool-call 分片/usage/finish_reason、`models.list()`（异步与同步）、`extra_body` 与具名参数、typed 状态/连接/超时错误、`Retry-After`、`AsyncStream.close()`。
  - 请求投影：`build_chat_completions_request` 固定保留字段优先级、`default_params` 具名/`extra_body` 分类、`max_output_tokens` 覆盖顺序、`stream_options` 条件发送、attachment role 不泄漏、认证由 SDK `api_key` 负责。
  - 漂移保护：`SDK_CHAT_OPTION_NAMES` 断言是已安装 `create()` 签名的子集且不含 `extra_*`/`timeout` 控制参数。
- 旧 provider 用例：`uv run python -m pytest tests/test_openai_compatible_provider.py tests/test_openai_compatible_provider_streaming.py -q` → 24 passed, 1 failed。
  - 唯一失败为既有用例 `test_catalog_contains_builtin_providers`：断言 `claude-openai-compatible` 在 `BUILTIN_PROVIDERS` 中，但 `infrastructure/providers/catalog.py` 无该定义。该文件与测试均未在本计划中修改，是工作树既有失败，不属于 M1 范围。
- 依赖边界：`uv run python -m pytest tests/test_import_boundaries.py -q` → 8 passed。
- 编译：`uv run python -m compileall infrastructure/providers/chat_completions.py` 通过。

### Milestone 2

- Batch 1：`uv run python -m pytest tests/test_openai_compatible_provider_streaming.py tests/test_openai_compatible_provider.py tests/test_runtime_integration.py -q` → 28 passed（`test_openai_compatible_provider.py` 1 个既有失败见下）。
  - `chat_completions.py` 经真实 SDK `httpx.MockTransport` 消费 typed stream；文本/tool-call 分片、usage、finish_reason、attachment 不泄漏、tool_result 投影与保留字段优先级均有断言。
  - `factory.py` 注入按 `ResolvedProviderConfig` 构建的 `AsyncOpenAI`（`timeout`、`max_retries=0`、`base_url`、`default_headers`、占位 `api_key`）。
- Batch 2：`uv run python -m pytest tests/test_openai_sdk_provider_errors.py -q` → 13 passed。
  - 覆盖 401/403/429/5xx/413/上下文超限、连接、超时、中途断流的部分输出可见、`Retry-After` 有效/无效、`CancelledError` 原样传播、以及重试请求次数由 `ModelRetryRunner` 决定（SDK 不隐藏重试）。
- `uv run python -m pytest tests/test_model_retry.py tests/test_loop.py tests/test_runtime_integration.py tests/test_import_boundaries.py -q` → 30 passed。
- `uv run python -m compileall core services infrastructure application -q` 通过。
- 完整套件 `uv run python -m pytest tests -q` → 806 passed, 3 failed；3 个失败均为工作树既有、与本计划无关：`test_openai_compatible_provider.py::test_catalog_contains_builtin_providers`（catalog 缺 `claude-openai-compatible`，M1 已记录）、`tests/test_search_tools.py::test_registry_generates_search_tool_schemas_and_prompts`（prompt 前缀改为 `Purpose`）、`tests/test_conversation_view.py::test_unmounted_message_reenters_with_full_content`。三者均不经过本次修改的 provider 代码。

### Milestone 3

- Batch 1：`uv run python -m pytest tests/test_cli_connect.py tests/test_openai_compatible_provider.py -q` → 4 passed + 25 passed（后者仅剩 1 个既有 catalog 失败）。
  - 新增断言：`ModelCatalogClient.list_models()`、`fetch_models_for_connect`（含候选 fallback）与 `test_model_connection` 由 SDK `httpx.MockTransport` 观察 `/models`、`/chat/completions`；Ollama `/api/tags`、`/api/chat` 仍走隔离的 `transport`。
- Batch 2：`uv run python -m pytest tests/test_session_controller.py tests/test_session_interrupt_cleanup.py tests/test_cli_connect.py tests/test_import_boundaries.py -q` → 44 passed。
  - 新增断言：关闭会话恰好关闭一次 model client；热重载成功关闭旧 client、安装新 client；装配失败关闭新 client、保留旧配置。
- 生产引用检查：`rg -n 'HttpxAsyncHttpTransport|parse_sse_json_line|AsyncHttpTransport' infrastructure application core services` → 无匹配。
- 编译：`uv run python -m compileall core services infrastructure application ui -q` 通过。
- 完整套件 `uv run python -m pytest tests -q` → 815 passed, 3 failed（同上 3 个既有失败）。
- 后续清理（用户要求）：移除 `test_openai_compatible_provider.py` 中遗留的 `claude-openai-compatible` catalog 期望。本项目不实现 Claude/Anthropic 协议或配置，`catalog.py` 从未定义该 provider。重跑 `uv run python -m pytest tests -q` → 816 passed, 2 failed（仅剩 `test_search_tools`、`test_conversation_view` 两个与本计划无关的既有失败）。

## Artifacts And Notes

- [计划入口](./plan.md)、[实施批次](./execution.md)、[执行决策](./decisions.md)。
- 新增 `tests/test_openai_sdk_provider_contract.py`（M1 契约）。
- 新增 `infrastructure/providers/chat_completions.py::build_chat_completions_request` 与 `ChatCompletionsRequest`；M2 已用它驱动 SDK 调用。
- M2 新增 `infrastructure/providers/sdk_errors.py`：`provider_error_from_sdk_exception` 把 SDK 连接/超时/状态/解码异常归一化为 `ProviderError`，含 `Retry-After` 解析。
- M2 新增 `infrastructure/providers/chat_completions.py::build_async_openai_client`、`OpenAICompatibleChatCompletionsClient.aclose()`；`factory.py::create_model_client` 的注入 seam 由 `async_transport` 改为 `sdk_client`/`http_client`。
- M2 新增 `tests/sdk_test_support.py`（SDK+`httpx.MockTransport` 公共测试夹具）与 `tests/test_openai_sdk_provider_errors.py`。
- M3：`infrastructure/providers/model_catalog.py` 的标准路径改用同步 SDK client（新增 `build_sync_openai_client`）；`factory.py` 新增 `close_model_client`，`create_model_catalog_client` 注入 seam 改为 `sdk_client`/`http_client`；`application/session.py` 关闭与热重载接线；`http.py` 删除 `AsyncHttpTransport`、`HttpxAsyncHttpTransport`、`parse_sse_json_line`，仅保留 `UrllibHttpTransport`/`parse_json_object`/`provider_error_from_http_status`。`tests/sdk_test_support.py` 新增 `sync_sdk`，provider 测试新增模型发现/探测/Ollama 用例，`test_session_controller.py` 新增关闭与热重载用例。
- SDK 官方资料：[Python SDK README](https://github.com/openai/openai-python/blob/main/README.md)、[Chat Completions API](https://github.com/openai/openai-python/blob/main/src/openai/resources/chat/completions/completions.py)、[Models resource](https://github.com/openai/openai-python/blob/main/src/openai/resources/models.py)。这些 `main` 链接只供背景参考；Milestone 1 以锁定版本为准。

## Outcomes And Retrospective

Milestone 1（2026-09-24）完成：锁定 `openai==3.18.0` 并写回 `uv.lock`；用 `httpx.MockTransport` 固化 SDK 传输、typed stream、models 与错误契约；提取 `build_chat_completions_request` 固定请求投影与保留字段优先级。业务路径仍走自建 HTTP/SSE，SDK 尚未接管（M2）。剩余差距：模型流、标准模型发现、client 生命周期与文档对齐。

Milestone 2（2026-09-24）完成：`chat_completions.py` 改为消费 SDK typed stream（分片文本、工具调用聚合、usage、finish_reason、`output_interrupted`），`factory.py` 注入 `AsyncOpenAI`（`max_retries=0`、显式 `timeout`），SDK 异常在边界统一成 `ProviderError`（含状态码、上下文超限、`Retry-After`），`CancelledError` 原样传播，流在正常/异常/取消/提前 `aclose()` 时关闭。测试全部改走真实 SDK 的 `httpx.MockTransport`。剩余差距：标准模型发现与 `/connect` 探测、SDK client 的所有权/关闭/热重载、删除旧异步传输与 SSE parser、文档最终对齐（M3/M4）。

Milestone 3（2026-09-24）完成：`model_catalog.py` 的标准 `/models` 与 `/connect` 标准 Chat Completions 探测改由同步 SDK client 发出，保留候选 base URL 顺序与 Ollama 原生路径；应用拥有并复用 SDK client，`application/session.py` 在 worker/子任务/流结束后关闭它，热重载成功关闭旧 client、失败关闭新 client；`http.py` 删除无调用者的异步传输与 SSE parser，`httpx` 依赖因 MCP 直接导入而保留。剩余差距：端到端本地协议演练、完整文档对齐与计划归档（M4）。

目标是在不引入 SDK 到 core/services 的前提下，删除自建标准 API 传输/SSE 代码，并保持实际 agent 交互行为。完成每个里程碑时记录可观察结果和剩余差距。
