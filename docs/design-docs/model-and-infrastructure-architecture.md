# Model And Infrastructure Architecture

本文描述 `services/model/` 与 `infrastructure/` 的架构边界。模型 provider 必须隔离在 infrastructure 中，core 和 services 只依赖 provider-neutral 类型。

## Model Service Boundary

`services/model/client.py` 定义模型客户端协议：

```python
stream(snapshot) -> AsyncIterator[ModelStreamEvent]
```

`services/model/stream.py` 定义 provider-neutral streaming event：

- `content_delta`
- `tool_call_delta`
- `tool_call_completed`
- `message_completed`
- `usage`
- `error`

`services/model/types.py` 定义：

- `ModelUsage`
- `LLMResponse`
- `ProviderError`

`ProviderError` 携带 provider id、status code、error type 和 retryable 标记。当前 loop 会记录 provider error trace 后重新抛出；完整恢复流程仍是目标能力。

## Provider Adapter

`infrastructure/providers/chat_completions.py` 当前实现 OpenAI Chat Completions compatible streaming client。

它负责：

- 构造 provider payload。
- 将 `ContextSnapshot.system_prompt` 投影为 system message。
- 将内部 messages 投影为 Chat Completions wire messages。
- 附加 provider-visible tool schema。
- 解析 streaming content delta。
- 累积 streaming tool call delta。
- 生成 completed `ToolCall`。
- 生成完整 assistant message。
- 解析 usage。
- 将配置错误、响应错误和工具参数错误转为 `ProviderError`。

OneCode 内部 `role="tool_result"` 会在 provider adapter 中投影为 Chat Completions 所需的 `role="tool"` 消息。

## HTTP Transport

`infrastructure/providers/http.py` 提供小型 JSON HTTP transport，支持普通 JSON 请求和 streaming JSON lines。它把 HTTP、URL、timeout、invalid JSON 等错误转换为 provider-neutral `ProviderError`。

429 和 5xx 会标记为 retryable，但主循环的 rate-limit retry 和 retry exhaustion 仍是后续目标。

## Provider Catalog

`infrastructure/providers/catalog.py` 定义 OpenAI-compatible provider catalog。当前包含：

- `openai`
- `deepseek`
- `glm`
- `minimax`
- `siliconflow`
- `gemini`
- `claude-openai-compatible`
- `custom`

`infrastructure/providers/model_catalog.py` 通过 provider 的 `/models` 端点发现模型，并解析为 `ProviderModel`。

`infrastructure/providers/connection.py` 提供 provider connection option 列表，是未来 CLI `/connect` flow 的基础。

## Provider Factory

`infrastructure/providers/factory.py` 从 `.env` 创建模型客户端和模型 catalog client。应用装配层调用 factory，`core/` 不直接依赖 factory。

## Config

`infrastructure/config/env.py` 从项目根目录 `.env` 读取运行时配置，例如：

- 默认 provider
- 模型名
- base URL
- API key
- timeout
- 额外 headers
- 默认请求参数

OneCode 只从 `.env` 读取模型 provider 配置，不从系统环境变量或项目 JSON/TOML 配置读取；dotenv interpolation 已禁用。

## Filesystem Infrastructure

`infrastructure/filesystem/paths.py` 提供跨平台路径处理底层函数。更高层的 sandbox boundary 和 guard policy 仍属于 `services/guard/`。

## 依赖约束

- `core/` 只依赖 `services/model/client.py` 的协议和 provider-neutral event。
- provider adapter 可以依赖 `services/context`、`services/model` 和 `services/tools` 类型来完成协议转换。
- `infrastructure/` 不能依赖 `core/`。
- 新 provider 支持应进入 `infrastructure/providers/`，不应修改主循环对模型响应的理解。
