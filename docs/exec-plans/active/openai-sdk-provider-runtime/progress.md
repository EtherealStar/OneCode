# Progress

实施者在每个停止点更新此文件，并遵循仓库根目录 [PLANS.md](../../../../PLANS.md)。

## Current State

- Status: Milestone 1 complete; Milestone 2 not started.
- Current milestone: Milestone 1, SDK 与请求契约（已完成）。
- Last updated: 2026-09-24 Asia/Shanghai.
- Working tree: 计划创建前已有大量已修改文件；不可将这些改动视为本计划成果，也不可重置。

## Milestones And Batches

- [x] Milestone 1: 锁定 SDK 与可重复的请求契约。
  - [x] Batch 1: 固定依赖与验证 SDK 接口。
  - [x] Batch 2: 明确请求参数与错误契约。
- [ ] Milestone 2: 模型流通过 SDK，运行时事件保持稳定。
  - [ ] Batch 1: 替换模型客户端内部调用。
  - [ ] Batch 2: 错误、重试与跨轮行为。
- [ ] Milestone 3: 标准模型 API 与客户端生命周期收口。
  - [ ] Batch 1: 模型发现和连接探测。
  - [ ] Batch 2: 所有权、关闭和旧传输删除。
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

## Artifacts And Notes

- [计划入口](./plan.md)、[实施批次](./execution.md)、[执行决策](./decisions.md)。
- 新增 `tests/test_openai_sdk_provider_contract.py`（M1 契约）。
- 新增 `infrastructure/providers/chat_completions.py::build_chat_completions_request` 与 `ChatCompletionsRequest`；`_build_payload` 改为经其 `to_body()` 产出，尚未切换 SDK 调用（M2）。
- SDK 官方资料：[Python SDK README](https://github.com/openai/openai-python/blob/main/README.md)、[Chat Completions API](https://github.com/openai/openai-python/blob/main/src/openai/resources/chat/completions/completions.py)、[Models resource](https://github.com/openai/openai-python/blob/main/src/openai/resources/models.py)。这些 `main` 链接只供背景参考；Milestone 1 以锁定版本为准。

## Outcomes And Retrospective

Milestone 1（2026-09-24）完成：锁定 `openai==3.18.0` 并写回 `uv.lock`；用 `httpx.MockTransport` 固化 SDK 传输、typed stream、models 与错误契约；提取 `build_chat_completions_request` 固定请求投影与保留字段优先级。业务路径仍走自建 HTTP/SSE，SDK 尚未接管（M2）。剩余差距：模型流、标准模型发现、client 生命周期与文档对齐。

目标是在不引入 SDK 到 core/services 的前提下，删除自建标准 API 传输/SSE 代码，并保持实际 agent 交互行为。完成每个里程碑时记录可观察结果和剩余差距。
