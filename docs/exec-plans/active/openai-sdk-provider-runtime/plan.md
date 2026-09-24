# 将 OpenAI 兼容模型请求交给 Python SDK

本计划遵循仓库根目录的 [PLANS.md](../../../../PLANS.md)。实施中须持续更新本计划包的决策、进度和验证证据。

## Purpose

OneCode 用户继续用现有 `.env` 配置 OpenAI 兼容服务，并通过 CLI/TUI 获得实时文本、工具调用、用量和错误恢复。底层标准 Chat Completions 和 Models API 请求改由 OpenAI Python SDK 发出和解码；OneCode 继续管理 agent 循环、上下文、权限、工具执行、重试决策和会话。用本地模拟 OpenAI API 的测试证明请求确实经过 SDK，且工具续轮与流式行为没有退化。

## Scope

涉及 `infrastructure/providers/`、`infrastructure/config/env.py`、`application/runtime.py`、`application/session.py`、`services/model/` 的接口和相关测试、`pyproject.toml`/`uv.lock`，以及最终与实现相符的模型供应商架构文档。标准 `/chat/completions` 流和标准 `/models` 调用由 SDK 承担；`/connect` 的端点选择、Ollama `/api/tags` 和 `/api/chat` 等非 OpenAI API 请求仍由基础设施层承担。不会把 SDK 的类型或异常传入 `core/`、`services/`，不会改成 Responses API、Agents SDK，也不扩展图像等产品能力。

接入承诺是：服务满足本计划使用的 OpenAI Chat Completions 与 Models API 协议时，配置 `base_url`、模型、密钥即可运行。不按供应商逐个设计兼容分支，也不以供应商兼容矩阵作为验收条件。服务自身不满足协议时，由其负责修正。

## Design References

- [根架构](../../../../architecture.md) 和 [核心原则](../../../design-docs/core-beliefs.md)：主循环薄、provider 隔离于 infrastructure、保留结构化数据。
- [模型供应商架构](../../../design-docs/model-provider-architecture.md)：`ModelClient`、`ModelStreamEvent`、`ProviderError` 与现有配置契约；其中“缓冲全部 events”与当前代码不符，实施时修正。
- [上下文架构](../../../design-docs/context-architecture.md) 与 [附件架构](../../../design-docs/attachment-architecture.md)：`ContextSnapshot` 投影及内部 `attachment` 不进入模型请求。
- [工具运行时](../../../design-docs/tool-runtime-architecture.md)、[权限架构](../../../design-docs/permission-architecture.md) 与 [核心运行时](../../../design-docs/core-runtime-architecture.md)：工具执行、guard/permission、模型工具调用后的续轮仍归 OneCode。
- [可观测性架构](../../../design-docs/observability-architecture.md)：重试 trace 和错误日志由运行时产生。
- [SDK 调研报告](../../../references/openai-python-sdk-migration-research.md)：SDK 能力与原生运行时的职责划分。实施以锁定版本源码和本地契约测试为准。

## Context And Orientation

`infrastructure/providers/chat_completions.py` 当前构造请求，并借助 `infrastructure/providers/http.py` 的 `HttpxAsyncHttpTransport` 发起 HTTP、逐行解析 SSE，再生成 `ModelStreamEvent`。`infrastructure/providers/model_catalog.py` 与 `/connect` 通过同步 `UrllibHttpTransport` 获取模型列表或测试连通性。`infrastructure/providers/factory.py` 读取 `.env`，`application/runtime.py` 装配和热重载 model client，`application/session.py` 负责关闭会话。`services/model/retry.py` 在每个流事件到来时立即转发，并对可重试的 `ProviderError` 决定重试；失败尝试的部分输出可能已可见。这里的“适配器”指把 SDK 对象映射到这些 OneCode 类型的基础设施代码。

SDK 管理 HTTP 连接、序列化、SSE 解析和标准 API 响应对象。OneCode 仍须把内部消息与工具 schema 投影成请求，把分片工具参数合并为 `ToolCall`，生成 `message_completed`，并维护已有重试与错误语义。标准模型列表可用 SDK `models.list()`；端点探测策略和非标准模型列表格式不能交给该 API。

## Plan Of Work

先建立锁定 SDK 版本的本地契约，随后替换模型流，再迁移标准模型发现和连接探测，最后理顺客户端生命周期、删除失去调用者的传输代码并更新文档。每个里程碑都在 [execution.md](./execution.md) 中有批次和验证命令。

### Milestone 1: 锁定 SDK 与可重复的请求契约

在不访问真实服务的条件下，证明锁定 SDK 对自定义 `base_url`、额外 headers、超时、`max_retries=0`、Chat Completions typed stream、标准 `models.list()` 以及 HTTP mock 的具体行为。记录所选版本和实际请求。现有业务路径尚未迁移时，此里程碑也可独立验收。

### Milestone 2: 模型流通过 SDK，运行时事件保持稳定

从 `ContextSnapshot` 到 SDK Chat Completions 流再到 `ModelStreamEvent` 完成闭环。增量文本立即可见；多工具分片、工具结果续轮、usage 可缺失、完成原因、上下文超限、认证/限流/服务端/网络/超时异常、取消和流关闭均由本地协议测试覆盖。SDK 不进行隐藏重试，OneCode 的 `ModelRetryRunner` 仍是唯一重试决策者。

### Milestone 3: 标准模型 API 与客户端生命周期收口

标准模型列表和 `/connect` 标准 Chat Completions 探测改走 SDK。OneCode 仍选择候选 base URL、整理 `ProviderModel`，并为 `/api/tags` 等非标准端点保留独立请求路径。应用复用模型 SDK client，在配置热重载和关闭时释放旧 client，正在运行的模型流与子任务先结束。移除无调用者的自建异步传输、SSE 解析及相关测试；`httpx` 若仍被 MCP 或 SDK mock 测试直接使用则保留依赖。

### Milestone 4: 跨模块验收与文档对齐

通过完整测试、依赖边界测试和一次本地模拟端点的 CLI/运行时交互：用户输入触发模型工具调用，OneCode 执行工具并把结果回送，模型流式输出最终答案。更新模型供应商架构图、文件职责、SDK/运行时所有权及真实的部分输出重试语义。验收不依赖真实 API 凭证。

## Validation And Acceptance

在仓库根目录运行 `uv sync --dev`、定向 provider/stream/retry/connect/session 测试、`uv run python -m pytest tests/test_import_boundaries.py -q` 与 `uv run python -m pytest tests -q`，均应通过。本地 HTTP mock 应观察到 `/chat/completions` 和 `/models` 请求由 SDK client 发出，配置的 timeout、headers 和请求参数生效；SDK 异常在适配器出口全部变成 `ProviderError`；`core/` 不导入 SDK；一次工具调用的声明、执行结果与续轮答案在 transcript 中正确配对。具体命令、边界样例和失败后的重试方法见 [execution.md](./execution.md)。

## Recovery

工作树当前有大量既有改动，执行者先记录 `git status --short`，只修改本计划涉及的文件，不重置或覆盖其他改动。按里程碑渐进替换，每批通过本地 mock 测试再删除旧实现。SDK 某项行为不符合假设时，先用锁定版本的隔离测试复现，再更新 [decisions.md](./decisions.md) 和批次；不要在主循环加入供应商特例，也不要保留两条永久模型请求路径。

## Related Documents

- [Execution](./execution.md)
- [Decisions](./decisions.md)
- [Progress](./progress.md)

## Change Note

2026-09-23：首次建立计划。范围根据现有代码和 SDK 调研扩至标准模型发现与连通性探测，并纳入热重载时的客户端关闭。
