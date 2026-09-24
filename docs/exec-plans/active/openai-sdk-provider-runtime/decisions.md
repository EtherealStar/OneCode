# Decisions

本文件记录本次执行的跨模块选择。长期契约在 [模型供应商架构](../../../design-docs/model-provider-architecture.md) 中维护。

## 2026-09-23: SDK 只接管标准 API 客户端

Decision: 标准 Chat Completions 请求、SSE 解码和标准 Models API 请求由 OpenAI Python SDK 负责；OneCode 继续管理 `ModelStreamEvent` 投影、工具执行、上下文、权限、重试决策、模型探测顺序及非标准端点。

Context: [调研报告](../../../references/openai-python-sdk-migration-research.md) 与当前 `infrastructure/providers/` 显示存在重复 HTTP/SSE 实现；SDK 不知道 OneCode 的事件和安全边界。

Rationale: 消除可由 SDK 维护的协议机械工作，保留 agent runtime 自身语义。实现验收只针对 OpenAI 协议，不逐供应商加兼容路径。

Consequences: `chat_completions.py` 和标准模型发现改写；`core/`/`services/` 的 SDK 导入必须为零。

## 2026-09-23: 保持 Chat Completions 和低层 typed chunk 流

Decision: 本次继续使用 `/chat/completions` 与 `chat.completions.create(stream=True)`。SDK 处理 SSE 与响应类型；OneCode 保留跨 chunk 的工具调用参数聚合。

Context: Responses API 的历史输入、tool call/result、输出 item 与续轮契约均不同；SDK 的高级 `.stream()` helper 提供另一套事件与累积语义，不能直接替代现有 `tool_call_delta` 和 `message_completed`。

Rationale: 限定本次变化在 provider 边界，避免同时迁移模型协议和会话格式。SDK 仍承担传输、SSE 和 typed chunk 解析。

Consequences: Responses API 不在此计划；工具参数 JSON 对象校验仍在 adapter。

## 2026-09-23: OneCode 是重试权威

Decision: SDK client 显式 `max_retries=0`、`timeout=ResolvedProviderConfig.timeout_seconds`；`ModelRetryRunner` 保留当前非缓冲、部分输出可见的重试方式。重试时 SDK 只发一次请求。

Context: SDK 有默认自动重试和较长默认 timeout；现有 OneCode runner 管理 attempt trace、backoff、reactive compact 与可见状态。`model-provider-architecture.md` 把 runner 误写成缓冲式，与 `services/model/retry.py` 不符。

Rationale: 避免双层重试、隐藏请求次数和默认超时漂移，同时维持用户可见实时流。

Consequences: 实施时更新架构说明，并在本地 mock 中断言请求次数、部分输出和错误分类。

## 2026-09-23: 标准模型发现使用 SDK，探测流程留在 OneCode

Decision: 对选中的标准 `/models` 候选，用 SDK Models resource；`/connect` 标准 chat 试探用 SDK Chat Completions；候选 base URL 的顺序和 Ollama 原生 `/api/tags`、`/api/chat` 由 OneCode 决定。

Context: `infrastructure/providers/model_catalog.py` 当前同时负责端点探测、OpenAI JSON 请求和 Ollama 原生格式。

Rationale: SDK 能接管标准请求，但不能表达非标准数据格式或应用的探测策略。

Consequences: 同步 SDK client 必须有确定的关闭边界；专有 endpoint 的最小 HTTP 函数可保留。

## 2026-09-23: 配置与客户端所有权

Decision: `.env` 继续是唯一 provider 配置来源。应用拥有并复用模型 SDK client；session rebind 和子任务借用它。热重载成功切换后释放旧 client，关闭会话时释放当前 client；无密钥的本地兼容端使用 SDK 构造占位值。计划基线依赖为 `openai==3.18.0`，以 `uv.lock` 固定传递依赖。

Context: SDK client 持有连接池；当前 `with_model_config()` 构造新 model client，但旧 client 没有关闭流程。官方发布的 [3.18.0 版本](https://pypi.org/project/openai/3.18.0/) 在 2026-09-22 发布，需对该版本实测 API。

Rationale: 复用连接池，避免热重载泄漏和依赖 SDK 的环境变量默认值。选择日期明确的版本便于复现。

Consequences: Milestone 1 必须核对 3.18.0 的真实接口；如不满足，先记录证据并修订版本/方案。

## 2026-09-24: Milestone 1 实测 openai==3.18.0 接口

Decision: 保持锁定 `openai==3.18.0` 并按计划迁移。隔离测试确认：`AsyncOpenAI`/`OpenAI` 接受 `api_key`、`base_url`、`timeout`、`max_retries`、`default_headers`、`http_client`；`chat.completions.create(stream=True)` 返回 typed `AsyncStream`，具备 `close()` 与 `aclose()`；`models.list()` 返回 `SyncPage[Model]`/异步页并有 `.data`；`extra_body` 字段与具名参数一起进入请求 JSON；`max_retries=0` 时一个可重试状态码只发一次 HTTP 请求。错误对象为 `AuthenticationError`/`PermissionDeniedError`/`RateLimitError`/`InternalServerError`/`BadRequestError`/`APIStatusError`（均带 `status_code`），连接与超时分别为 `APIConnectionError`、`APITimeoutError`；`Retry-After` 可从 `exc.response.headers` 读取。

Context: 批 1 核对锁定版本时发现 3.18.0 的默认 HTTP 客户端由新依赖 `httpx2`（`httpx2==2.13.1`）提供，而非项目直接依赖的 `httpx`。SDK 仍通过 `_httpx2.py` 兼容旧 `httpx` client，因此可用 `httpx.MockTransport` 注入测试。

Rationale: 计划明确要求用 `httpx.MockTransport` 锁定契约；实测证明该路径在 3.18.0 下可用，无需改用 `httpx2` 或新增测试依赖。项目仍需保留显式 `httpx` 依赖（`services/mcp/manager.py` 直接导入）。

Consequences: 批 1/批 2 测试使用 `httpx.AsyncClient(transport=httpx.MockTransport(...))` 注入 SDK。生产实现仍由 `AsyncOpenAI` 自建默认 client；M3 决定是否显式传入 client 以控制关闭边界。请求投影已提取为 `chat_completions.build_chat_completions_request`（`ChatCompletionsRequest.named/extra_body`），M2 用它驱动 SDK 调用。

## Open Questions

当前没有阻止撰写计划的产品决策。实施中的 SDK 细节由锁定版本的隔离测试确定；若测试推翻以上选择，先更新本文件再继续。
